"""Evaluation: retrieval hit-rate, citation accuracy and refusal accuracy, on two separate sets.

* ``eval_set.json``     the original 12 questions; thresholds were tuned while looking at these.
* ``eval_heldout.json`` 15 new questions (4 that must be refused), frozen before the retrieval changes
  and never used for tuning. ``eval_heldout.sha256`` detects silent edits.

python -m doc_qa_citations.evaluate [--retriever tfidf|embedding] [--config config.json]
"""
from __future__ import annotations

import argparse
import hashlib
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
    should_refuse = [r for r, c in zip(rows, cases) if c["expect"] is None]

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 3) if b else None

    return {
        "cases": n,
        "answerable": answerable,
        "should_refuse": len(should_refuse),
        "hit_rate_at_1": ratio(hit1, answerable),
        f"hit_rate_at_{composer.k}": ratio(hitk, answerable),
        "citation_accuracy": ratio(cite_ok, answerable),
        "refusal_accuracy": ratio(sum(not r["found"] for r in should_refuse), len(should_refuse)),
        "refusal_correctness": ratio(refusal_ok, n),  # answered-vs-refused correct, over all cases
        "passed": sum(r["pass"] for r in rows),
        "rows": rows,
    }


def heldout_unchanged(path: Path) -> bool:
    lock = path.with_suffix(".sha256")
    return not lock.exists() or hashlib.sha256(path.read_bytes()).hexdigest() == lock.read_text().strip()


def _print(name: str, res: dict, k: int) -> None:
    print(f"\n== {name} ==")
    for r in res["rows"]:
        flag = "PASS" if r["pass"] else "FAIL"
        print(f"{flag} [{r['role']:10s}] found={str(r['found']):5s} score={r['score']:.2f} {r['q']}")
    print({kk: v for kk, v in res.items() if kk != "rows"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--eval", default="eval_set.json", help="original (tuned-on) set")
    ap.add_argument("--heldout", default="eval_heldout.json", help="held-out set, never used for tuning")
    ap.add_argument("--retriever", choices=["tfidf", "embedding"], default=None)
    ap.add_argument("--out", default="output/eval_results.json")
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    logging.basicConfig(level=cfg.get("log_level", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    composer = build(cfg, retriever=a.retriever)
    held = Path(a.heldout)
    if not heldout_unchanged(held):
        print(f"WARNING: {held} no longer matches its .sha256 lock: results are not a clean held-out measure")
    out = {"retriever": a.retriever or cfg.get("retriever", "tfidf"),
           "dev": evaluate(composer, json.loads(Path(a.eval).read_text()))}
    _print("dev set (thresholds tuned on this set: optimistic)", out["dev"], composer.k)
    if held.exists():
        out["heldout"] = evaluate(composer, json.loads(held.read_text()))
        _print("held-out set (not used for tuning)", out["heldout"], composer.k)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
