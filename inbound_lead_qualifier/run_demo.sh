#!/usr/bin/env bash
# Start the local webhook, post the 10 sample leads, stop the server, then run tests.
# The CRM is encrypted at rest. If you have not set CRM_ENCRYPTION_KEY, a throwaway key is
# generated for this run only (never saved), so the demo always shows the encrypted path.
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${CRM_ENCRYPTION_KEY:-}" ]; then
  CRM_ENCRYPTION_KEY="$(python3 -m inbound_lead_qualifier genkey)"
  export CRM_ENCRYPTION_KEY
  echo "Using a throwaway demo CRM key for this run (not saved anywhere)."
fi
python3 -m inbound_lead_qualifier demo
echo "--- CRM file on disk (ciphertext, first bytes):"
head -c 60 output/crm_leads.csv.enc; echo
tmp="$(mktemp)"
python3 -m inbound_lead_qualifier export-crm --out "$tmp"
rm -f "$tmp"
python3 -m pytest -q
