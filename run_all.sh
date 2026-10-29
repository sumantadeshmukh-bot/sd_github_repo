#!/usr/bin/env bash
# Run all three demos and their tests (offline by default).
set -euo pipefail
cd "$(dirname "$0")"
for d in invoice_to_erp doc_qa_citations inbound_lead_qualifier; do
  echo "================ $d ================"
  "./$d/run_demo.sh"
done
