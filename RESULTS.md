# Results (29 Oct 2026, offline mode, Python 3.13)

All four tools ran end to end with **no API keys and no network calls to external services**: `./run_all.sh` exited 0. **144 tests passed and 2 were skipped** (invoice_to_erp 25 + 2 skipped, doc_qa_citations 32, inbound_lead_qualifier 35, evidence_pack 49, online_check 3). The two skips are the live-model test (no API key) and the OCR test (Tesseract not installed). Before this round the three demos had 24 tests.

<!-- online-run:start -->
## Online run (real model)

**Not run.** No `GEMINI_API_KEY` or `ANTHROPIC_API_KEY` was available in the build environment, so none of the three demos has been tested against a real model, and the retry, timeout and cost-log code was tested only with stubbed HTTP responses. To record a real run: set a key as an environment variable and run `python3 online_check.py --write` from the repo root. It replaces this section with the model name, calls, tokens, latency, estimated cost, parse failures and fallbacks per demo.
<!-- online-run:end -->

## Demo 1: invoice_to_erp
Processed 6 invoices: **OK = 3, REVIEW-only = 0, EXCEPTION = 3**. The 3 clean invoices (7 line rows) went into `erp_import_bills.csv`, `tally_purchase_vouchers.xml` (3 balanced vouchers) and `zoho_bills.csv` (7 rows). The new multi-rate invoice (inv06) has lines at 5%, 12% and 18%, and passes the per-rate checks. The Tally and Zoho layouts are **assumptions, not tested against a live Tally or Zoho**.

| File | Invoice No | Status | Why |
|---|---|---|---|
| inv01_kaveri.pdf | KPT/2026-27/0142 | OK | Intra-state KA to KA, CGST 9% + SGST 9%, Rs. 40,108.20 |
| inv02_sahyadri.pdf | SCS-INV-0098 | OK | Inter-state MH to KA, IGST 18%, SAC codes, Rs. 71,390.00 |
| inv03_coromandel.pdf | CPC-7781 | EXCEPTION + review | Line 2: 120 x 65.00 = 7,800.00 but the invoice shows 8,500.00. Grand total 35,000.00 does not equal 29,750.00 + 5,355.00 (= 35,105.00). Place of supply was not printed, so it was inferred from the buyer's GSTIN (confidence 0.60 means human review) |
| inv04_yamuna.pdf | YOS/1029 | EXCEPTION | Supplier GSTIN `07ABCPY3456R1ZZ` fails the checksum (expected `S`, found `Z`) |
| inv05_kaveri_resend.pdf | KPT/2026-27/0142 | EXCEPTION | Duplicate of inv01 (same supplier GSTIN, invoice no. and FY 2026-27), sent again in a different layout |
| inv06_hoysala_multirate.pdf | HSG/0233 | OK | Intra-state KA to KA, three GST slabs (5%, 12%, 18%), Rs. 75,240.00 |

The checksum code was also checked against three widely published sample GSTINs: all three matched. Those GSTINs are not included in the repo.

## Demo 2: doc_qa_citations
33 chunks from 6 documents, 3 of them confidential. Results are now reported on two separate sets (TF-IDF retriever, offline extractive answers):

| Metric | Old set (12 questions: 10 answerable, 2 must refuse) | Held-out set (15 questions: 11 answerable, 4 must refuse) |
|---|---|---|
| Retrieval hit-rate @1 | 1.00 (10/10) | **0.91** (10/11) |
| Retrieval hit-rate @3 | 1.00 | 1.00 |
| Citation accuracy | 1.00 | 0.91 |
| Refusal accuracy (must-refuse questions refused) | 1.00 (2/2) | 1.00 (4/4: 2 out of scope, 2 blocked by access control) |

- The **old set is tuned-on-test**: the thresholds and stemmer were adjusted while looking at it, so 1.00 is optimistic. The **held-out set** was written and committed before the retrieval changes and never used for tuning (a hash lock and a test guard it).
- The one held-out miss: "How long do you keep my order history?" (public role) retrieved another privacy-notice section that also mentions order history, not the Data retention section. The label was not changed after seeing this.
- Optional embedding retriever (local LSA backend, `min_score` 0.82 chosen on the old set only): the same results on both sets (old 12/12; held-out 14/15, same miss). The sentence-transformers backend was not tested.
- The lakh/crore normaliser did not change these numbers; unit tests check that "50 lakh", "Rs. 50,00,000" and "5000000" match the same chunk, and that an employee still gets no confidential answer.
- 15 questions is a small set: one miss moves hit-rate by 9 points. Treat it as a smoke test, not a benchmark.

## Demo 3: inbound_lead_qualifier
10 leads were posted to the local webhook at `127.0.0.1`.

| Outcome | Count | Leads |
|---|---|---|
| QUALIFIED: Hot | 3 | CFO, invoice extraction (100). Ops Manager, SOP Q&A (81.5). Director IT, RAG assistant (100) |
| QUALIFIED: Warm | 3 | Founder, 1-10 staff, lead qualification (52.5). Analyst, workflow automation (68). VP Finance, no budget yet (56) |
| QUALIFIED: Nurture | 1 | Marketing executive, Gmail address, chatbot, Rs. 2 lakh budget (27) |
| WINDOW_EXPIRED | 1 | Scored Hot (97.5), but the enquiry was 14 days old, past the 7-day window. No draft created |
| PARKED_NO_CONSENT | 1 | Saved to `parked_leads.csv` with only id, time and reason (no name or email) |
| REJECTED_INVALID | 1 | HTTP 400: invalid email, and the phone is not an Indian mobile number |

The run produced 7 draft replies, all headed "DRAFT - NOT SENT". The audit log masks email and phone (for example `a***@deccanspice.example`, `******01`).

New in this version: the CRM was written as `crm_leads.csv.enc` (Fernet, throwaway demo key) and exported back to 8 rows; 35 tests cover rate limiting (`429` + `Retry-After`), oversized bodies (`413`), missing or invalid length (`411`, `400`), wrong content type (`415`), over-nested JSON, JSON log lines without personal data, wrong or missing keys, key rotation and the dev-mode plaintext fallback. The rate limiter and size limits were tested with local requests only, not under real load.

## Tool 4: evidence_pack
Both synthetic samples ran (49 tests):

| | Nimbus Pay (Demo), fintech, 120 staff | Hemavati Finance (Demo), NBFC |
|---|---|---|
| Input | 8 use cases (YAML) | 12 use cases (CSV + org file) |
| Risk tiers (High / Medium / Low) | 5 / 0 / 3 | 6 / 4 / 2 |
| Overall score (1-4) | 2.56 (weakest areas: Accountability 2.0, Vendor AI risk 2.2) | 3.01 (weakest AI security 2.5 and Vendor AI risk 2.4) |
| Gaps (High) | 78 (14) | 117 (15) |
| 90-day plan actions | 24 | 23 |
| Report / board summary pages (LibreOffice) | 7 / 2 | 7 / 2 |
| Top theme | No exit plan or fallback for the AI vendor (6 use cases) | No exit plan or fallback for the AI vendor (9 use cases) |

In the fintech sample the tool assigns UC-01 (KYC document extraction) High while the client's own register says Medium: sensitive identity data affecting customers triggers rule T2. This difference is shown in the Inventory sheet. The sample answers were written by the consultant to produce a realistic spread, so these scores say nothing about real firms.

