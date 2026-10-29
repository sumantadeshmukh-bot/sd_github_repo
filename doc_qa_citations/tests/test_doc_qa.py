import json
from pathlib import Path

import pytest

from doc_qa_citations.answer import NOT_FOUND, Composer
from doc_qa_citations.core import build, load_config
from doc_qa_citations.corpus import load_corpus
from doc_qa_citations.evaluate import evaluate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def composer() -> Composer:
    return build(load_config(ROOT / "config.json"), ROOT)


def test_chunks_have_headings_and_access():
    chunks = load_corpus(ROOT / "docs")
    assert {c.doc_id.split("-")[0] for c in chunks} >= {"HR", "FIN", "IT", "LEGAL", "CS"}
    conf = [c for c in chunks if c.access == "confidential"]
    assert {c.section for c in conf} == {"Incident escalation and regulator reporting",
                                         "Privileged access reviews", "Approval limits"}


def test_employee_never_retrieves_confidential(composer):
    for q in ("break-glass admin credentials vault", "approval limits CFO board audit committee"):
        hits = composer.r.search(q, "employee", k=33)
        assert all(h.chunk.access != "confidential" for h in hits)


def test_answer_has_exact_citation(composer):
    a = composer.ask("How many days of sick leave per year?", "employee")
    assert a.found and a.citations[0].chunk_id == "HR-LEAVE-01#sick-leave-0" and "12 days" in a.text


def test_out_of_scope_refused(composer):
    a = composer.ask("Who won the cricket match yesterday?", "leadership")
    assert not a.found and a.text == NOT_FOUND and not a.citations


def test_department_filter(composer):
    hits = composer.r.search("approval", "leadership", department="HR")
    assert all(h.chunk.department == "HR" for h in hits)


def test_eval_set_meets_bar(composer):
    res = evaluate(composer, json.loads((ROOT / "eval_set.json").read_text()))
    assert res["cases"] == 12 and res["hit_rate_at_3"] >= 0.9 and res["refusal_correctness"] == 1.0
