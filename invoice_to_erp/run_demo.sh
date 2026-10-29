#!/usr/bin/env bash
# Generate synthetic invoices, run the pipeline, then the tests. Offline unless an API key is set.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m invoice_to_erp --generate
python3 -m pytest -q
