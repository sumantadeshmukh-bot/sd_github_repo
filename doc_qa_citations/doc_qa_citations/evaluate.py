"""Evaluation: retrieval hit-rate, citation accuracy and refusal correctness.

python -m doc_qa_citations.evaluate [--config config.json] [--eval eval_set.json]
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .answer import Composer
from .core import build, load_config


def evaluate(composer: Composer, cases: list[dict]) -> dict:
    rows, hit1, hitk, cite_ok, refusal_ok, answerable = [], 0, 0, 0, 0, 0
    for c in cases:
        hits = composer.r.search(c["q"], c["role"], None, composer.k)
        ids = [h.chunk.chunk_id for h in hits]
        ans = composer.ask(c["q"], c["role"])
        should_answer = c["expect"] is not None
        refusal_correct = ans.found == should_answer
        refusal_ok += refusal_correct
        if should_answer:
            answerable += 1
            hit1 += bool(ids) and ids[0] == c["expect"]
            hitk += c["expect"] in ids
            cite_ok += any(ct.chunk_id == c["expect"] for ct in ans.citations)
        rows.append({"q": c["q"], "role": c["role"], "expect": c["expect"], "top": ids[:1],
                     "found": ans.found, "cited": [ct.chunk_id for ct in ans.citations],
                     "score": round(ans.confidence, 3), "pass": refusal_correct and
                     (not should_answer or any(ct.chunk_id == c["expect"] for ct in ans.citations))})
    n = len(cases)
    return {
        "cases": n,
        "answerable": answerable,
        "hit_rate_at_1": round(hit1 / answerable, 3),
        f"hit_rate_at_{composer.k}": round(hitk / answerable, 3),
        "citation_accuracy": round(cite_ok / answerable, 3),
        "refusal_correctness": round(refusal_ok / n, 3),
        "passed": sum(r["pass"] for r in rows),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--eval", default="eval_set.json")
    ap.add_argument("--out", default="output/eval_results.json")
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    logging.basicConfig(level=cfg.get("log_level", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    res = evaluate(build(cfg), json.loads(Path(a.eval).read_text()))
    for r in res["rows"]:
        flag = "PASS" if r["pass"] else "FAIL"
        print(f"{flag} [{r['role']:10s}] found={str(r['found']):5s} score={r['score']:.2f} {r['q']}")
    print({k: v for k, v in res.items() if k != "rows"})
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
