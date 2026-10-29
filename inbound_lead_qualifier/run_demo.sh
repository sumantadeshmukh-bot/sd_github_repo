#!/usr/bin/env bash
# Start the local webhook, post the 10 sample leads, stop the server, then run tests.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m inbound_lead_qualifier demo
python3 -m pytest -q
