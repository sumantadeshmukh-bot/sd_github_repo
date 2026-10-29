"""Tests for: amount normaliser, held-out evaluation, optional embedding retriever, LLM usage log."""
import hashlib
import json
from pathlib import Path

import pytest

from doc_qa_citations import llm
from doc_qa_citations.answer import NOT_FOUND
from doc_qa_citations.core import build, load_config
from doc_qa_citations.evaluate import evaluate, heldout_unchanged
from doc_qa_citations.normalize import normalize_amounts, parse_amount
from doc_qa_citations.retriever import EmbeddingRetriever, analyze

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- normaliser
@pytest.mark.parametrize("a,b", [
    ("50 lakh", "₹50,00,000"),
    ("50 lakhs", "Rs. 50,00,000"),
    ("5 crore", "₹5,00,00,000"),
    ("Rs 3 lakh", "INR 300000"),
    ("25 thousand", "Rs. 25,000"),
    ("5k", "₹5,000"),
    ("2.5 lakh", "Rs. 2,50,000"),
    ("10L", "10 lakh"),
])
def test_equivalent_amount_spellings_match(a, b):
    assert parse_amount(a) == parse_amount(b) is not None


def test_normalize_rewrites_only_money():
    out = normalize_amounts("12 days of leave; claims above rs. 5,00,000 or 5 lakh; 10,000 staff; 6 hours")
    assert "amt500000" in out and out.count("amt500000") == 2
    assert "12 days" in out and "10000 staff" in out and "6 hours" in out


def test_rs_inside_a_word_is_not_currency():
    assert normalize_amounts("within 48 hours 5 more") == "within 48 hours 5 more"
    assert parse_amount("hours 5") is None


def test_analyzer_gives_same_token_for_lakh_and_rupee_forms():
    assert "amt5000000" in analyze("above 50 lakh") and "amt5000000" in analyze("Rs. 50,00,000")


def test_retrieval_matches_amount_written_differently():
    c = build(load_config(ROOT / "config.json"), ROOT)
    # the policy writes "Rs. 50,00,000"; the user writes "50 lakh" / "5000000"
    for q in ("Who approves orders above 50 lakh?", "Who approves purchase orders above Rs 5000000?"):
        a = c.ask(q, "leadership")
        assert a.found and a.citations[0].chunk_id == "FIN-PROC-05#approval-limits-0", q


def test_amount_does_not_leak_confidential_to_employee():
    c = build(load_config(ROOT / "config.json"), ROOT)
    assert c.ask("Who approves orders above 50 lakh?", "employee").text == NOT_FOUND


# ---------------------------------------------------------------- held-out evaluation
def test_heldout_set_shape_and_lock():
    path = ROOT / "eval_heldout.json"
    cases = json.loads(path.read_text())
    assert len(cases) == 15 and sum(c["expect"] is None for c in cases) == 4
    assert heldout_unchanged(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (ROOT / "eval_heldout.sha256").read_text().strip()


def test_heldout_questions_do_not_overlap_dev_set():
    dev = {c["q"].lower() for c in json.loads((ROOT / "eval_set.json").read_text())}
    assert not dev & {c["q"].lower() for c in json.loads((ROOT / "eval_heldout.json").read_text())}


def test_heldout_metrics_reported_separately():
    c = build(load_config(ROOT / "config.json"), ROOT)
    res = evaluate(c, json.loads((ROOT / "eval_heldout.json").read_text()))
    assert (res["cases"], res["answerable"], res["should_refuse"]) == (15, 11, 4)
    assert res["refusal_accuracy"] == 1.0           # all 4 must-refuse questions were refused
    assert res["hit_rate_at_3"] >= 0.9              # honest floor, not a tuned target
    assert 0.8 <= res["hit_rate_at_1"] <= 1.0


def test_refusal_accuracy_counts_only_must_refuse_cases():
    c = build(load_config(ROOT / "config.json"), ROOT)
    res = evaluate(c, [{"q": "Who won the cricket match?", "role": "employee", "expect": None},
                       {"q": "How many days of sick leave per year?", "role": "employee",
                        "expect": "HR-LEAVE-01#sick-leave-0"}])
    assert res["refusal_accuracy"] == 1.0 and res["hit_rate_at_1"] == 1.0 and res["should_refuse"] == 1


# ---------------------------------------------------------------- embedding retriever (optional)
@pytest.fixture(scope="module")
def emb():
    cfg = load_config(ROOT / "config.json")
    cfg["embedding"]["backend"] = "lsa"  # deterministic, no model download
    return build(cfg, ROOT, retriever="embedding")


def test_default_retriever_is_tfidf():
    assert load_config(ROOT / "config.json")["retriever"] == "tfidf"
    assert not isinstance(build(load_config(ROOT / "config.json"), ROOT).r, EmbeddingRetriever)


def test_embedding_retriever_answers_with_citation(emb):
    assert emb.r.backend == "lsa"
    a = emb.ask("How many days of sick leave per year?", "employee")
    assert a.found and a.citations[0].chunk_id == "HR-LEAVE-01#sick-leave-0"


def test_embedding_retriever_keeps_access_control(emb):
    hits = emb.r.search("break-glass admin credentials vault approval limits CERT-In", "employee", k=33)
    assert hits and all(h.chunk.access != "confidential" for h in hits)
    assert emb.ask("Within how many hours must cyber incidents be reported to CERT-In?", "employee").found is False


def test_embedding_retriever_refuses_out_of_scope(emb):
    assert emb.ask("What is the share price target for next quarter?", "leadership").found is False


def test_embedding_meets_same_bar_on_both_sets(emb):
    for name in ("eval_set.json", "eval_heldout.json"):
        res = evaluate(emb, json.loads((ROOT / name).read_text()))
        assert res["refusal_accuracy"] == 1.0 and res["hit_rate_at_3"] >= 0.9, name


def test_unknown_retriever_rejected():
    with pytest.raises(ValueError):
        build(load_config(ROOT / "config.json"), ROOT, retriever="bm25")


def test_sentence_transformers_backend_is_explicit_when_missing(monkeypatch):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("sentence_transformers"):
            raise ImportError("not installed")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    chunks = build(load_config(ROOT / "config.json"), ROOT).r.chunks
    roles = load_config(ROOT / "config.json")["role_access"]
    assert EmbeddingRetriever(chunks, roles, backend="auto").backend == "lsa"  # auto falls back quietly
    with pytest.raises(RuntimeError):                                          # explicit request does not
        EmbeddingRetriever(chunks, roles, backend="sentence-transformers")


# ---------------------------------------------------------------- LLM usage log (shared adapter)
def test_usage_summary_empty_without_calls():
    llm.reset_usage()
    s = llm.usage_summary()
    assert s["calls"] == 0 and s["estimated_cost_usd"] is None


def test_config_carries_pricing_with_check_note():
    cfg = load_config(ROOT / "config.json")["llm"]
    assert "CHECK CURRENT PRICING" in cfg["_pricing_note"] and cfg["pricing"]
