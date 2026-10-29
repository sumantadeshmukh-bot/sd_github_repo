"""Run each demo once against the real model (Gemini or Claude) and record the outcome.

    export ANTHROPIC_API_KEY=...        # or GEMINI_API_KEY=...   (read from the environment only)
    python online_check.py              # print the results
    python online_check.py --write      # also replace the "Online run" section of RESULTS.md

With no key it prints a clear SKIPPED message and exits 0. Recorded per demo: provider and model,
number of calls, tokens, latency, estimated cost (prices come from each demo's config.json and must
be checked against current provider pricing), parse failures and fallbacks to the offline logic.
No prompts or responses are written anywhere; only counts and metrics.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
for demo in ("invoice_to_erp", "doc_qa_citations", "inbound_lead_qualifier"):
    sys.path.insert(0, str(ROOT / demo))

START, END = "<!-- online-run:start -->", "<!-- online-run:end -->"
IST = timezone(timedelta(hours=5, minutes=30))


def _cfg(demo: str) -> dict[str, Any]:
    return json.loads((ROOT / demo / "config.json").read_text())


def _adapter_for(source, llm_module):
    """Each demo has its own copy of llm.py (own usage log, own config), so every demo needs its own adapter.

    ``source`` is a factory taking the demo's llm module, or a ready adapter (used by tests)."""
    return source(llm_module) if callable(source) and not hasattr(source, "available") else source


def check_invoice(source, tmp: Path) -> dict[str, Any]:
    from invoice_to_erp import llm
    from invoice_to_erp.llm import OfflineAdapter
    from invoice_to_erp.pipeline import run

    adapter = _adapter_for(source, llm)

    cfg = _cfg("invoice_to_erp")
    llm.configure(cfg.get("llm"))
    src = ROOT / "invoice_to_erp" / cfg["input_dir"]
    run(src, tmp / "inv_off", cfg["review_confidence_threshold"], adapter=OfflineAdapter())
    llm.reset_usage()
    run(src, tmp / "inv_on", cfg["review_confidence_threshold"], adapter=adapter)
    load = lambda d: json.loads((tmp / d / "invoices.json").read_text())  # noqa: E731
    off, on = load("inv_off"), load("inv_on")
    status = lambda rows: {r["invoice"]["source_file"]: r["validation"]["status"] for r in rows}  # noqa: E731
    parsers = [r["invoice"]["parser"] for r in on]
    return {
        "invoices": len(on),
        "parsed_by_llm": sum(p.startswith("llm-") for p in parsers),
        "fell_back_to_regex": sum(not p.startswith("llm-") for p in parsers),
        "status_matches_offline": sum(status(on)[k] == v for k, v in status(off).items()),
        "usage": llm.usage_summary(),
    }


def check_doc_qa(source) -> dict[str, Any]:
    from doc_qa_citations import llm
    from doc_qa_citations.core import build, load_config
    from doc_qa_citations.evaluate import evaluate

    adapter = _adapter_for(source, llm)

    base = ROOT / "doc_qa_citations"
    composer = build(load_config(base / "config.json"), base)
    composer.adapter = adapter
    llm.reset_usage()
    out: dict[str, Any] = {}
    for name, f in (("dev", "eval_set.json"), ("heldout", "eval_heldout.json")):
        cases = json.loads((base / f).read_text())
        res = evaluate(composer, cases)
        modes = [composer.ask(c["q"], c["role"]).mode for c in cases if c["expect"]]
        out[name] = {k: res[k] for k in ("cases", "hit_rate_at_1", "citation_accuracy", "refusal_accuracy")}
        out[name]["answers_by_llm"] = sum(m.startswith("llm-") for m in modes)
        out[name]["fell_back_to_extractive"] = sum(not m.startswith("llm-") for m in modes)
    out["usage"] = llm.usage_summary()
    return out


def check_leads(source, tmp: Path) -> dict[str, Any]:
    from inbound_lead_qualifier import llm
    from inbound_lead_qualifier.llm import OfflineAdapter
    from inbound_lead_qualifier.pipeline import LeadPipeline

    adapter = _adapter_for(source, llm)

    cfg = _cfg("inbound_lead_qualifier")
    leads = json.loads((ROOT / "inbound_lead_qualifier" / "samples" / "leads.json").read_text())
    llm.configure(cfg.get("llm"))

    def run(adapter_, sub: str):
        c = {**cfg, "output_dir": str(tmp / sub)}
        pipe = LeadPipeline(c, adapter=adapter_)
        return [pipe.process(lead) for lead in leads], pipe

    base, _ = run(OfflineAdapter(), "lead_off")
    llm.reset_usage()
    online, pipe = run(adapter, "lead_on")
    polished = sum(1 for r in online if r.draft_path and
                   (tmp / "lead_on" / "drafts" / f"{r.lead_id}.txt").read_text() !=
                   (tmp / "lead_off" / "drafts" / f"{r.lead_id}.txt").read_text())
    return {"leads": len(online), "status_and_tier_match_offline":
            sum((a.status, a.tier) == (b.status, b.tier) for a, b in zip(online, base)),
            "drafts_polished_by_llm": polished, "usage": llm.usage_summary()}


def run_checks(source) -> dict[str, Any]:
    """``source``: factory ``f(llm_module) -> adapter`` (real runs: ``lambda m: m.get_adapter()``) or one adapter."""
    from invoice_to_erp import llm as inv_llm

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        ref = _adapter_for(source, inv_llm)
        return {"provider": ref.name, "model": getattr(ref, "model", "?"),
                "invoice_to_erp": check_invoice(source, tmp), "doc_qa_citations": check_doc_qa(source),
                "inbound_lead_qualifier": check_leads(source, tmp)}


def render(res: dict[str, Any]) -> str:
    def u(x: dict[str, Any]) -> str:
        cost = x["estimated_cost_usd"]
        return (f"{x['calls']} calls ({x['failed_calls']} failed), {x['input_tokens']} in / {x['output_tokens']} out tokens, "
                f"{x['total_latency_s']} s, est. cost {'n/a (no price for this model in config)' if cost is None else f'${cost:.4f}'}, "
                f"{x['parse_failures']} parse failures")

    inv, qa, ld = res["invoice_to_erp"], res["doc_qa_citations"], res["inbound_lead_qualifier"]
    now = datetime.now(IST).strftime("%d %b %Y %H:%M IST")
    lines = [START, "## Online run (real model)", "",
             f"Run on {now} with provider `{res['provider']}`, model `{res['model']}`. Prices in each config.json are "
             "illustrative: **check current pricing** before quoting costs.", "",
             "| Demo | Result | LLM usage |", "|---|---|---|",
             f"| invoice_to_erp | {inv['parsed_by_llm']}/{inv['invoices']} parsed by the LLM, {inv['fell_back_to_regex']} fell back to regex; "
             f"{inv['status_matches_offline']}/{inv['invoices']} statuses equal the offline run | {u(inv['usage'])} |"]
    for name in ("dev", "heldout"):
        q = qa[name]
        lines.append(f"| doc_qa_citations ({name}) | hit@1 {q['hit_rate_at_1']}, citation accuracy {q['citation_accuracy']}, "
                     f"refusal accuracy {q['refusal_accuracy']}; {q['answers_by_llm']} answers by LLM, "
                     f"{q['fell_back_to_extractive']} fell back to extractive | {u(qa['usage']) if name == 'dev' else 'same run'} |")
    lines.append(f"| inbound_lead_qualifier | {ld['status_and_tier_match_offline']}/{ld['leads']} status+tier equal offline "
                 f"(scoring is rules-based); {ld['drafts_polished_by_llm']} drafts polished | {u(ld['usage'])} |")
    lines += ["", END]
    return "\n".join(lines)


def write_results(section: str, path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if START in text and END in text:
        head, rest = text.split(START, 1)
        text = head + section + rest.split(END, 1)[1]
    else:
        text = text.rstrip() + "\n\n" + section + "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="update the Online run section of RESULTS.md")
    a = ap.parse_args()
    from invoice_to_erp.llm import get_adapter

    adapter = get_adapter()
    if not adapter.available:
        print("SKIPPED: no GEMINI_API_KEY or ANTHROPIC_API_KEY in the environment (or LLM_PROVIDER=offline). "
              "Nothing was sent anywhere.")
        return 0
    section = render(run_checks(lambda llm_module: llm_module.get_adapter()))
    print(section)
    if a.write:
        write_results(section, ROOT / "RESULTS.md")
        print("\nRESULTS.md updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
