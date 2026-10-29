#!/usr/bin/env bash
# Build the evidence pack for both synthetic sample registers, then run the tests.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m evidence_pack samples/fintech_120.yaml --out output/fintech_120
python3 -m evidence_pack samples/nbfc_12.csv --org samples/nbfc_org.yaml --out output/nbfc_12
python3 -m pytest -q
