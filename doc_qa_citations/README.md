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
python3 -m doc_qa_citations.evaluate                       # 12-question evaluation -> output/eval_results.json
```

## Switching to Gemini or Claude
Set `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`. You can also set `LLM_PROVIDER` and `*_MODEL`. The LLM only sees chunks that passed the role filter and the refusal gates, and it must cite their `[chunk_id]`s. If an answer has no citation, or cites a chunk it was not given, it is thrown away and the extractive answer is used instead. A `NOT_FOUND` reply from the LLM is respected. The online path is untested here because no API key was available.

## Limitations
- TF-IDF matches words, not meaning: "50 lakh" will not match "Rs. 50,00,000". For production, add embeddings or hybrid search, and a reranker.
- The thresholds (`min_score` 0.15, `min_coverage` 0.5) and the small stemmer were tuned on the same 12-question set used for evaluation. Expect lower scores on new questions, and build a held-out set from real user questions.
- Extractive answers copy sentences word for word. They are accurate, but they do not summarise or combine information across sections.
- IDF statistics are computed over the whole corpus, including confidential chunks. This reveals word frequencies, not content. For strict isolation, build a separate index per access level.

## Compliance notes (India)
- **DPDP Act 2023, s.8(5)** requires reasonable security safeguards. Filtering by role before retrieval is one such control. Log queries without storing personal data.
- The **DPDP Rules 2025** were notified in Nov 2025, and most obligations start in phases after that (*to check: the exact commencement dates*).
- Offline mode makes no external calls. Online mode sends policy text to the LLM provider, so check the provider's data processing agreement, retention terms and where the data is processed.
- For banks, NBFCs and other RBI-regulated clients, align with **RBI's FREE-AI framework (Aug 2025)**, which covers governance, explainability and human oversight. Cited answers and the evaluation report help show this.
- The policy content is synthetic. Any legal references in it (for example, the CERT-In 6-hour reporting rule or the Maternity Benefit Act) are only there as sample policy text.
