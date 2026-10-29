#!/usr/bin/env bash
# Scripted Q&A demo (incl. access-control refusal), the 12-question evaluation, then tests.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m doc_qa_citations
python3 -m doc_qa_citations.evaluate
python3 -m pytest -q
