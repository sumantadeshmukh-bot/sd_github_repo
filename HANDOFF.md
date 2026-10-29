# Handoff: hardening of the three demos and the new evidence pack (29 Oct 2026)

Branch: `claude/gifted-mendel-cms2p8` (to check: the brief named `hardening-and-evidence-pack`; this cloud session may only push to its assigned branch, so the pull request comes from that one).

## What was built
**Job 1: the three demos**
| Demo | Added |
|---|---|
| All three (`llm.py`) | Timeouts, retries with exponential backoff (timeouts, HTTP 429/5xx), per-call usage log (tokens, latency, attempts, parse failures, estimated cost), per-model prices in `config.json` labelled "check current pricing". `online_check.py` at the repo root runs each demo once against a real model and writes the results into `RESULTS.md` when a key is present. |
| invoice_to_erp | Several GST rates on one invoice (per-line rate, per-rate slab validation), Tally Prime voucher XML and Zoho Books bills CSV behind mapping files in `mappings/`, optional OCR (`--ocr`), new sample invoice with 5%/12%/18% lines. |
| doc_qa_citations | Held-out set of 15 new questions (4 must be refused), frozen and hash-locked; old and held-out results reported separately (hit-rate and refusal accuracy); lakh/crore/rupee amount normaliser; optional embedding retriever (`--retriever embedding`, TF-IDF stays default). |
| inbound_lead_qualifier | Per-client rate limiting (`429`), request size and shape limits (`411/413/415/400`), socket timeout, structured JSON logs, CRM encrypted at rest with Fernet (key from `CRM_ENCRYPTION_KEY`, key rotation, loud dev-mode fallback, `required` switch), `genkey` and `export-crm` commands. |

**Job 2: `evidence_pack/`** loads a YAML or CSV AI use-case register, tiers each use case with configurable rules, scores 8 areas (1-4) with evidence notes from 27 checks, and generates `evidence_pack.xlsx` (Inventory, Area scores, Gaps, 90-day plan, Assumptions), `evidence_pack.docx` (7 pages), `heatmap.png` and `board_summary.docx` (2 pages). Two synthetic samples are included and generated: a 120-person fintech (8 use cases) and a mid-size NBFC (12 use cases).

**Repo hygiene:** `.gitignore` added and the committed `__pycache__` files removed from git; the `run_demo.sh` and `run_all.sh` scripts are now executable (they were not, so the README commands failed); pinned `requirements.txt` at the top level and per tool; the old plaintext `crm_leads.csv` output was replaced by the encrypted one.

## Test results (offline, Python 3.13)
| Suite | Result |
|---|---|
| invoice_to_erp | 26 passed, 2 skipped (live-model test needs a key; OCR test needs Tesseract) |
| doc_qa_citations | 32 passed |
| inbound_lead_qualifier | 35 passed |
| evidence_pack | 52 passed (includes LibreOffice page counts: report 7 pages, board summary 2) |
| online_check (root `tests/`) | 4 passed |
| **Total** | **149 passed, 2 skipped, 0 failed** |

`./run_all.sh` exits 0 and prints "SKIPPED" for the online check because there is no key.

## Sample outputs produced
- `evidence_pack/output/fintech_120/` and `evidence_pack/output/nbfc_12/`: xlsx, docx, heatmap.png, board_summary.docx.
- `invoice_to_erp/output/`: `erp_import_bills.csv`, `tally_purchase_vouchers.xml`, `zoho_bills.csv`, `invoices.json`, `exceptions_report.md`.
- `doc_qa_citations/output/eval_results.json` (old and held-out sets, separately).
- `inbound_lead_qualifier/output/`: `crm_leads.csv.enc` (encrypted with a throwaway key, so it cannot be decrypted), drafts, audit log, summary.
- Key metrics are in `RESULTS.md`.

## Known limitations
- **No real-model run.** No API key was available, so online mode, retries and cost logging were tested only with stubbed HTTP responses. `RESULTS.md` says "Not run".
- **Evidence pack scores come from what the client says.** Nothing is tested. The scoring scheme (27 checks, weights, bands, severity formula, tier factors) is my design, not an RBI-prescribed method.
- The held-out set is 15 questions written by the same author as the system. One question was missed and the label was left unchanged.
- The Tally XML and Zoho CSV layouts are assumptions; OCR is untested on real scans; the sentence-transformers backend of the embedding retriever is untested.
- The rate limiter is in memory, per process. Only the CRM file is encrypted (drafts and parked leads are not). The whole CRM is re-encrypted on every write.
- Report page counts were measured in LibreOffice; Word may differ by a page.

## Open points for you (all to check)
1. **FREE-AI references (to check).** The recommendation numbers per area (rec. 14, 23, 15, 16, 19-20, 21-24, the Sutras) are exactly as you gave them and are labelled "to verify against the RBI FREE-AI report text before client use" everywhere. Verify against the report text and edit `evidence_pack/config/areas.yaml`.
2. **RBI draft MRM guidance (Jun 2026) is not mapped (to check).** The Assessment offer mentions it, but no paragraph-level mapping was supplied, so none was invented. Add references under `additional_references`.
3. **Scoring weights, bands, tier rules and validation age limits (to check).** Review `config/checks.yaml`, `scoring.yaml` and `tiering.yaml` with a real client register before presenting scores as meaningful. For example, "monitoring yes" counts as partial, and personal data is assumed when nothing is declared.
4. **Tally and Zoho templates (to check).** Compare with a real Tally voucher export and the client's current Zoho Bills template (`invoice_to_erp/README.md` has the table of assumed fields).
5. **Model names and prices (to check).** Default model names in `llm.py` (`gemini-2.5-flash`, `claude-sonnet-4-5`) may be out of date; override with `GEMINI_MODEL` / `ANTHROPIC_MODEL`. The prices in each `config.json` are illustrative; check current provider pricing.
6. **Run `online_check.py --write` once you have a key (to check)**, then review the results it writes into `RESULTS.md`.
7. **Branch name (to check).** Rename the branch or recreate the PR from `hardening-and-evidence-pack` if you want that exact name.
8. **Regulatory notes carried over (to check).** The DPDP Rules 2025 commencement dates and the TRAI follow-up window in the READMEs were already marked "to check"; they were not re-verified.
9. **CRM key handling (to check).** Decide where `CRM_ENCRYPTION_KEY` lives (secret manager), the rotation schedule, and whether drafts also need encryption.
10. **Sample content (to check).** All sample companies, people and vendors are fictional and marked "(Demo)"; scan once more before sending any output to a client.
