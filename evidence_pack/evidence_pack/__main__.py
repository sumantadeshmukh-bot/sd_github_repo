"""CLI: python -m evidence_pack INPUT.(yaml|csv) [--org org.yaml] [--out DIR]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loader import RegisterError
from .pipeline import build_pack


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the Bank-ready AI evidence pack from an AI use-case register")
    ap.add_argument("input", type=Path, help="register as .yaml/.yml or .csv")
    ap.add_argument("--org", type=Path, default=None, help="organisation details + org-level controls (YAML), for CSV input")
    ap.add_argument("--out", type=Path, default=Path("output"), help="output directory")
    a = ap.parse_args(argv)
    try:
        res = build_pack(a.input, a.out, a.org)
    except RegisterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    sc = res.scored
    print(f"{sc.register.org.name}: {len(sc.register.use_cases)} use cases, overall {sc.overall}/4, "
          f"{len(sc.gaps)} gaps ({sum(g.severity == 'High' for g in sc.gaps)} High), {len(sc.plan)} plan actions")
    for k, v in res.files.items():
        print(f"  {k:14s} -> {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
