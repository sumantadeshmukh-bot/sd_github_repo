# AI consulting demos (India): index

Three small, private demos that run offline, built for showing to prospects. Each one runs end to end **today without any API key**, using deterministic offline logic. Each one switches to Gemini or Claude when you set `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`. All data is synthetic: fictional companies, fictional people, and fake GSTINs with valid check digits.

| # | Demo | Client problem it solves | Run |
|---|---|---|---|
| 1 | [invoice_to_erp](invoice_to_erp/README.md) | GST purchase bills keyed in by hand, with errors and duplicate payments | `./invoice_to_erp/run_demo.sh` |
| 2 | [doc_qa_citations](doc_qa_citations/README.md) | Staff can't find policy answers, and "AI chat" tools make answers up | `./doc_qa_citations/run_demo.sh` |
| 3 | [inbound_lead_qualifier](inbound_lead_qualifier/README.md) | Slow, inconsistent follow-up on web enquiries, with consent risk | `./inbound_lead_qualifier/run_demo.sh` |

Run everything with `./run_all.sh`. You need Python 3.11 or later with `pydantic`, `pdfplumber`, `reportlab`, `scikit-learn` and `pytest`. The measured results are in [RESULTS.md](RESULTS.md).

## Shared design
- **`llm.py` (the same file in each demo):** a `LLMAdapter` interface with `GeminiAdapter`, `ClaudeAdapter` and `OfflineAdapter`. `get_adapter()` picks one based on `LLM_PROVIDER` or whichever key is set, and falls back to offline. The online adapters use only the standard library and are only called when a key is set. Any LLM failure falls back to the offline result.
- **The LLM is never the final authority.** Invoice fields are cross-checked against regex extraction. Q&A citations must point to chunks the LLM was actually given. Lead scoring stays rules-based.
- **Settings live in `config.json`**, logging goes through `logging`, the code uses type hints, and there are pytest tests.

## Answering "anyone can build this with AI"
The value is in the checks around the model, not the model call: the GSTIN checksum and place-of-supply tax logic, refusals and an evaluation set, a role filter applied before retrieval, DPDP consent evidence and an audit trail. These pieces are what make an AI workflow safe to run in an Indian finance or compliance setting.
