"""CLI: python -m invoice_to_erp [--generate] [--config config.json]"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .generate import generate
from .pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic GST invoice -> ERP import demo")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--generate", action="store_true", help="(re)create the synthetic sample PDFs first")
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    logging.basicConfig(level=cfg.get("log_level", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    in_dir, out_dir = Path(cfg["input_dir"]), Path(cfg["output_dir"])
    if args.generate or not any(in_dir.glob("*.pdf")):
        generate(in_dir)
    s = run(in_dir, out_dir, cfg["review_confidence_threshold"])
    print(f"\nProcessed {s.total}: OK={s.ok} REVIEW={s.review} EXCEPTION={s.exceptions}")
    for k, v in s.outputs.items():
        print(f"  {k:10s} -> {v}")


if __name__ == "__main__":
    main()
