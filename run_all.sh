#!/usr/bin/env bash
# Run all four tools and their tests (offline by default).
set -euo pipefail
cd "$(dirname "$0")"
for d in invoice_to_erp doc_qa_citations inbound_lead_qualifier evidence_pack; do
  echo "================ $d ================"
  "./$d/run_demo.sh"
done
echo "================ online_check ================"
python3 online_check.py   # skips cleanly when no API key is set
python3 -m pytest -q tests
