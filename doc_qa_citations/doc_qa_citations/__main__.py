"""CLI: python -m doc_qa_citations --role employee "How many sick days do I get?"

Without a question it runs a short scripted demo (including an access-control refusal).
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .core import build, load_config

DEMO = [
    ("employee", "How many days of earned leave can I carry forward?"),
    ("public", "Which items cannot be returned?"),
    ("employee", "What is the daily allowance on outstation travel?"),
    ("leadership", "Who approves purchase orders above 50 lakh?"),
    ("employee", "Who approves purchase orders above 50 lakh?"),
    ("employee", "What is the canteen menu on Friday?"),
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Policy Q&A with citations and role-based access")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--role", default="employee")
    ap.add_argument("--department", default=None, help="optional metadata filter, e.g. HR, Finance")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--retriever", choices=["tfidf", "embedding"], default=None,
                    help="tfidf (default) or the optional local embedding retriever")
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    logging.basicConfig(level=cfg.get("log_level", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    composer = build(cfg, retriever=a.retriever)
    pairs = [(a.role, a.question)] if a.question else DEMO
    for role, q in pairs:
        print(f"\n--- role={role}")
        print(composer.ask(q, role, a.department).render())


if __name__ == "__main__":
    main()
