# Demo 2: doc_qa_citations (policy Q&A with exact citations and role-based access)

A local question-answering assistant over six synthetic company policies for "Tarangini Retail Pvt Ltd", a fictional company. Every answer cites the document, section and chunk it came from. If the evidence is weak, the assistant says **"Not found in the documents available to your role."** It never guesses.

```
markdown docs -> heading-aware chunks (+department, access level)
  -> TF-IDF index (scikit-learn) -> role/department filter BEFORE scoring
  -> refusal gates (score + term coverage) -> extractive answer (offline) | grounded LLM answer (online)
  -> citations [doc > section > chunk_id]
```

## Business value
- **Faster answers for staff and customers.** HR, travel, IT and refund questions are answered in seconds, with a citation anyone can check.
- **No made-up answers.** Weak evidence produces a refusal instead of a confident wrong answer, and an evaluation set measures how well this works.
- **Access control is built in.** An `employee` cannot retrieve `confidential` sections (incident escalation, privileged access, approval limits), and those chunks never reach an LLM prompt.

## Documents and roles
| Doc | Department | Access |
|---|---|---|
| Leave Policy (HR-LEAVE-01) | HR | internal |
| Travel and Expense (FIN-TRAVEL-02) | Finance | internal |
| IT Security (IT-SEC-03) | IT | internal, with 2 confidential sections |
| Privacy Notice, DPDP-style (LEGAL-PRIV-04) | Legal | public |
| Procurement (FIN-PROC-05) | Finance | internal, with 1 confidential section |
| Returns and Refunds (CS-REFUND-06) | Customer Service | public |

Roles are set in `config.json`: `public` sees public docs; `employee` sees public and internal; `leadership` sees everything. A section can only tighten access, using a heading tag such as `## Approval limits {access: confidential}`.

## How to run
```bash
cd doc_qa_citations
./run_demo.sh                                              # scripted Q&A, evaluation, tests
python3 -m doc_qa_citations --role employee "How many sick days do I get?"
python3 -m doc_qa_citations --role leadership --department Finance "Who approves orders above 50 lakh?"
python3 -m doc_qa_citations.evaluate                       # old set + held-out set, reported separately -> output/eval_results.json
python3 -m doc_qa_citations.evaluate --retriever embedding # same evaluation with the optional embedding retriever
pip install -r requirements.txt
```

## Switching to Gemini or Claude
Set `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`. You can also set `LLM_PROVIDER` and `*_MODEL`. The LLM only sees chunks that passed the role filter and the refusal gates, and it must cite their `[chunk_id]`s. If an answer has no citation, or cites a chunk it was not given, it is thrown away and the extractive answer is used instead. A `NOT_FOUND` reply from the LLM is respected. Online calls use a timeout and retry with backoff, and every call is logged to the usage log (tokens, latency, estimated cost from the illustrative prices in `config.json`: check current pricing). **The online path has not been run against a live model yet** (no API key was available); run `python3 ../online_check.py --write` from the repo root once a key is set.

## What changed in this version
- **Held-out evaluation set** (`eval_heldout.json`): 15 new questions, 4 of which must be refused (2 out of scope, 2 blocked by access control). It was written and committed **before** the retrieval changes and has not been used for tuning. `eval_heldout.sha256` locks it, and a test fails if the file is edited. Results are reported separately for the old (tuned-on) and the held-out set: hit-rate over the answerable questions, refusal accuracy over the must-refuse ones.
- **Amount normaliser** (`normalize.py`): "50 lakh", "Rs. 50,00,000", "INR 5000000" and "5 crore" / "Rs 5,00,00,000" become the same token in both documents and questions. Only numbers with a currency marker or a scale word are treated as money, so "12 days" is untouched.
- **Optional embedding retriever** (`--retriever embedding`, or `"retriever": "embedding"` in config). TF-IDF stays the default. The embedding backend uses `sentence-transformers` if it is installed and the model is cached locally (**not tested here**); otherwise it uses a local LSA embedding (TruncatedSVD over the TF-IDF matrix), which needs no download. Role filtering is unchanged. Its `min_score` (0.82) was chosen from the dev set only.
- **LLM hardening:** timeouts, retries with backoff, token and cost log.

## Evaluation results (offline, TF-IDF)
| Set | Questions | Hit-rate @1 | Hit-rate @3 | Citation accuracy | Refusal accuracy (must-refuse) |
|---|---|---|---|---|---|
| Old set (thresholds tuned on it: optimistic) | 12 (10 answerable, 2 must refuse) | 1.00 | 1.00 | 1.00 | 2 of 2 |
| Held-out set (not used for tuning) | 15 (11 answerable, 4 must refuse) | 0.91 (10/11) | 1.00 | 0.91 | 4 of 4 |

The one held-out miss is "How long do you keep my order history?" (public role). The top chunk was another privacy-notice section that also mentions order history, instead of the Data retention section. The label was not changed after seeing the result. The embedding retriever scores the same on both sets. With 11 answerable questions, one miss moves hit-rate by 9 points, so treat these as a smoke test and build a larger set from real user questions.

The amount normaliser did not change these numbers (the questions already overlapped the policy text); its effect is covered by unit tests that check that different spellings of one amount match.

## Limitations
- TF-IDF matches words, not meaning (amounts are now normalised, but synonyms such as "quotes" and "quotations" are not). The optional embedding retriever here is small and local; for production, test a proper embedding model or hybrid search, and a reranker.
- The thresholds (`min_score` 0.15, `min_coverage` 0.5) and the small stemmer were tuned on the old 12-question set, so its 1.00 is optimistic. The held-out set is only 15 questions and was written by the same person who built the system. Build a bigger set from real user questions.
- Extractive answers copy sentences word for word. They are accurate, but they do not summarise or combine information across sections.
- IDF statistics are computed over the whole corpus, including confidential chunks. This reveals word frequencies, not content. For strict isolation, build a separate index per access level.

## Compliance notes (India)
- **DPDP Act 2023, s.8(5)** requires reasonable security safeguards. Filtering by role before retrieval is one such control. Log queries without storing personal data.
- The **DPDP Rules 2025** were notified in Nov 2025, and most obligations start in phases after that (*to check: the exact commencement dates*).
- Offline mode makes no external calls. Online mode sends policy text to the LLM provider, so check the provider's data processing agreement, retention terms and where the data is processed.
- For banks, NBFCs and other RBI-regulated clients, align with **RBI's FREE-AI framework (Aug 2025)**, which covers governance, explainability and human oversight. Cited answers and the evaluation report help show this.
- The policy content is synthetic. Any legal references in it (for example, the CERT-In 6-hour reporting rule or the Maternity Benefit Act) are only there as sample policy text.
