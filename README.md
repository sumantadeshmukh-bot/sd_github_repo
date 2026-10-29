# AI consulting kit (India): index of four tools

Four small, private tools for an independent AI consultant selling to Indian firms. Tools 1-3 are offline demos that switch to Gemini or Claude when you set `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`. Tool 4 turns a client's AI use-case register into the evidence pack for the Sprint and the Assessment. All data is synthetic: fictional companies marked "(Demo)", `.example` domains, fake GSTINs with valid check digits, fake phone numbers.

| # | Tool | Client problem it solves | Run |
|---|---|---|---|
| 1 | [invoice_to_erp](invoice_to_erp/README.md) | GST purchase bills keyed in by hand, with errors and duplicate payments (multi-rate GST, Tally XML and Zoho CSV export, optional OCR) | `./invoice_to_erp/run_demo.sh` |
| 2 | [doc_qa_citations](doc_qa_citations/README.md) | Staff can't find policy answers, and "AI chat" tools make answers up (held-out evaluation, lakh/crore normaliser) | `./doc_qa_citations/run_demo.sh` |
| 3 | [inbound_lead_qualifier](inbound_lead_qualifier/README.md) | Slow, inconsistent follow-up on web enquiries, with consent risk (rate limits, JSON logs, encrypted CRM) | `./inbound_lead_qualifier/run_demo.sh` |
| 4 | [evidence_pack](evidence_pack/README.md) | "Are we ready for RBI-style AI governance questions?" (scored workbook, report, heatmap, board summary) | `./evidence_pack/run_demo.sh` |

Run everything with `./run_all.sh`. Python 3.11 or later; `pip install -r requirements.txt` (versions are pinned; each tool also has its own `requirements.txt`). Measured results are in [RESULTS.md](RESULTS.md); what was built, known limits and open points are in [HANDOFF.md](HANDOFF.md).

To test against a real model, set a key (environment variable only, never in the repo) and run `python3 online_check.py --write`. Without a key it prints "SKIPPED" and changes nothing.

## Shared design (tools 1-3)
- **`llm.py` (the same file in each demo):** a `LLMAdapter` interface with `GeminiAdapter`, `ClaudeAdapter` and `OfflineAdapter`. `get_adapter()` picks one from `LLM_PROVIDER` or whichever key is set, and falls back to offline. Online calls use a timeout, retry with exponential backoff and write a usage log (tokens, latency, estimated cost from per-model prices in `config.json`, **check current pricing**). Any LLM failure falls back to the offline result.
- **The LLM is never the final authority.** Invoice fields are cross-checked against regex extraction. Q&A citations must point to chunks the LLM was actually given. Lead scoring stays rules-based.
- **Settings live in `config.json`**, logging goes through `logging`, the code uses type hints, and there are pytest tests.

## What to show a client in 15 minutes
| Min | Show | Say |
|---|---|---|
| 0-2 | Open `evidence_pack/output/nbfc_12/heatmap.png` | "This is what an AI governance readiness review looks like for a 12-use-case NBFC. Every cell is a score you can trace to evidence." Ask which use cases they would expect to be red. |
| 2-5 | `board_summary.docx` (2 pages) | "This is what your board gets: the headline, five gaps, the decisions we need from them, and the 90-day plan." |
| 5-8 | `evidence_pack.xlsx`: Inventory (risk tier and why), then Gaps (owner, blank due date) | "Tiering is rule-based and you can change the rules. Every gap has an owner; you set the due dates." |
| 8-10 | Ask for **their** register (even a rough spreadsheet), run `python3 -m evidence_pack their.csv --out /tmp/theirs`, show what is missing | "Blank answers count as not evidenced. That is the honest starting position." |
| 10-12 | `./doc_qa_citations/run_demo.sh`: a cited answer, then the access-control refusal (employee vs leadership) | "No made-up answers. Weak evidence is a refusal. Here is the held-out evaluation, including the miss." |
| 12-14 | `./invoice_to_erp/run_demo.sh`: open `exceptions_report.md` and the Tally XML | "Bad GSTIN, wrong tax type, duplicate bill: caught before the ERP. Only clean bills are exported." |
| 14-15 | Close on the offers: **Sprint** (Rs. 40-75k, 10 days: inventory, cost control, DPDP data-flow map, ROI roadmap, one proof of concept) and **Assessment** (Rs. 2.5-8 lakh, 1-3 weeks: governance readiness against RBI FREE-AI). | "Which of your use cases would you want scored first?" |

Before any client demo: say that the references are working mappings (to verify), that scores are based on what the client tells you, and that this is readiness advice, not a formal audit.

## Answering "anyone can build this with AI"
The value is in the checks around the model, not the model call: the GSTIN checksum and place-of-supply tax logic, refusals and an evaluation set, a role filter applied before retrieval, DPDP consent evidence and an audit trail, and a scoring scheme a bank reviewer can follow line by line. These pieces are what make an AI workflow safe to run in an Indian finance or compliance setting.
