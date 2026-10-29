# Demo 1: invoice_to_erp (GST invoice PDFs to an ERP import file)

Turns supplier GST invoice PDFs into a clean ERP import file. Bills that fail checks are held back and listed in an exceptions report, so an accountant only looks at the problem bills.

```
PDFs -> pdfplumber text -> parser (regex offline | LLM structured output online, cross-checked)
     -> pydantic Invoice/LineItem -> validations -> erp_import_bills.csv + invoices.json + exceptions_report.md
```

## Business value
- **Less manual data entry.** Clean bills go straight into the import CSV. People only review the exceptions.
- **Fewer tax and payment errors.** Bad GSTINs, the wrong tax type (IGST vs CGST+SGST), arithmetic mistakes and duplicate bills are caught before they reach the books or get paid twice.
- **Every decision can be explained.** Each flag has a plain-English reason, and fields the parser is unsure about are marked for human review.

## What it checks
| Check | Rule |
|---|---|
| GSTIN format | 2-digit state + PAN pattern + entity + `Z` + check character |
| GSTIN checksum | Official mod-36 algorithm (alternating factors 1/2 over 0-9A-Z) |
| Tax type vs place of supply | Same state means CGST = SGST and no IGST. Different state means IGST only |
| Arithmetic | qty x rate = line amount. Sum of lines = taxable value. Tax = rate x taxable value. Grand total = taxable value + tax (Rs. 1 round-off allowed) |
| Duplicates | Supplier GSTIN + normalised invoice no. + financial year |
| Human review | Any field with confidence below 0.75 (for example, place of supply inferred from the buyer's GSTIN) |

## How to run
```bash
cd invoice_to_erp
./run_demo.sh                       # generate 5 PDFs, run the pipeline, run the tests
python3 -m invoice_to_erp           # pipeline only (generates PDFs if none exist)
```
Outputs go to `output/`: `erp_import_bills.csv` (generic), `tally_purchase_vouchers.xml`, `zoho_bills.csv`, `invoices.json` and `exceptions_report.md`. The sample PDFs (now 6, including a multi-rate invoice) are in `data/invoices/`.

```bash
pip install -r requirements.txt
python3 -m invoice_to_erp --ocr     # optional: OCR pages that have no text layer (needs pytesseract + the Tesseract program)
```

## Switching to Gemini or Claude
```bash
export ANTHROPIC_API_KEY=...        # or: export GEMINI_API_KEY=...
export LLM_PROVIDER=claude          # optional: force a provider (claude|gemini|offline)
export ANTHROPIC_MODEL=...          # optional: override the default model name
```
Online calls use a timeout and retry with exponential backoff (timeouts, HTTP 429 and 5xx; settings in the `llm` block of `config.json`). Every call is logged to `output/llm_usage.json`: input and output tokens, latency, attempts, parse failures and an estimated cost. The per-model prices in `config.json` are illustrative and labelled "check current pricing". No prompts or responses are logged. To record a real-model run, set a key and run `python3 ../online_check.py --write` from the repo root.

In online mode the LLM returns JSON that is validated against the same pydantic schema. The key fields (invoice no., GSTINs, taxable value, total) are then checked against the regex parse. If the two disagree, that field's confidence drops to 0.5, which triggers human review. If the LLM fails or returns invalid output, the regex result is used. The online adapters use only the standard library. **They have not been run against a live model yet** (no API key was available when this version was built); `RESULTS.md` records when that is done.

## What changed in this version
- **Several GST rates on one invoice.** Each line can carry its own rate (a `GST%` column) and the invoice prints one tax line per rate. The validator checks each rate's slab separately (tax at 12% = 12% of the lines taxed at 12%), flags tax charged at a rate no line carries, and the CSV, Tally and Zoho exports use the per-line rate. New sample: `inv06_hoysala_multirate.pdf` (5%, 12% and 18% lines).
- **Tally Prime voucher XML** (`tally_purchase_vouchers.xml`) and **Zoho Books bills CSV** (`zoho_bills.csv`), each driven by a mapping file in `mappings/` (ledger names, tags, column headers, date formats). Exception invoices are never exported.
- **Optional OCR** behind `--ocr` (or `"ocr": true`). It only touches pages with no text layer, and is skipped with a warning if `pytesseract` or the Tesseract program is missing.
- **LLM hardening:** timeouts, retries with backoff, token and cost log (see above).

## Export mappings: assumed fields to check with the client
Both mappings are **assumptions modelled on common templates, not verified against a client's system**. Before go-live, export a real purchase voucher from the client's Tally company, and download the current Bills import template from their Zoho Books organisation, then edit the mapping files until the output matches exactly.

| Item | Where | What is assumed | Check |
|---|---|---|---|
| Voucher type, date format (`YYYYMMDD`) | `mappings/tally_purchase.json` | A "Purchase" voucher with the ledger entry tag `LEDGERENTRIES.LIST` | The tag name and `OBJVIEW` differ by Tally version; the company name must match exactly |
| Ledger names | same | `Purchase GST {rate}%`, `Input CGST @ {rate}%`, `Input SGST @ {rate}%`, `Input IGST @ {rate}%`, `Round Off`, party ledger = vendor name | Every ledger must already exist in the client's company with these exact names (or the import fails) |
| Debit/credit signs | code | Party credited with the bill total; purchase and tax ledgers debited (negative amounts, `ISDEEMEDPOSITIVE` Yes) | Compare with a voucher the client's accountant has posted |
| GST fields (`PARTYGSTIN`, `PLACEOFSUPPLY`) | same | Tags are accepted as written | Tally may need `GSTREGISTRATIONTYPE` and state fields for the client's setup |
| Column headers and order | `mappings/zoho_bills.json` | Headers such as `Bill Date`, `GST Identification Number (GST IN)`, `Item Tax %`, `Account` | The current Zoho template may name or order columns differently, and may need extra columns (due date, tax exemption reason) |
| Dates | same | `YYYY-MM-DD` | Zoho accepts the format set in the organisation's settings |
| Account | same | A constant `Cost of Goods Sold` | Replace with the client's expense account |

## Limitations
- The offline parser is tuned to the three demo layouts. Real vendors need layout templates or the LLM mode, plus a labelled test set.
- OCR (`--ocr`) is a basic Tesseract pass and has **not been tested on real scanned bills**; its test is skipped where Tesseract is not installed. Expect lower confidence and more human review on scans.
- Multi-rate invoices work only when the invoice prints a rate per line and one tax line per rate. It does not check the HSN code against the rate schedule (GST rates changed from 22 Sep 2025, so keep rates in config).
- Duplicate detection only covers the current batch. In production, check against the ERP or a database.
- The generic CSV, the Tally XML and the Zoho CSV are all unverified against a live Tally or Zoho (see the table above).
- The multi-rate parser and the exporters were tested on synthetic invoices only.

## Compliance notes (India)
- Invoice particulars follow **CGST Act s.31 and CGST Rules 2017, Rule 46** (GSTIN, an invoice number unique per financial year, HSN/SAC, taxable value, rate, place of supply).
- Intra-state vs inter-state logic follows **IGST Act ss.7-8**, with place of supply under ss.10-12. This demo uses a simplified version.
- **E-invoicing (IRN/QR)** is mandatory above Rs. 5 crore aggregate turnover. This demo does not verify the IRN, so verify the signed QR through the IRP in production.
- Input tax credit eligibility depends on **GSTR-2B** reconciliation, which this demo does not cover.
- Bills from sole proprietors can contain personal data covered by the DPDP Act 2023. Offline mode keeps all processing on the machine. Online mode sends invoice text to the LLM provider, so you need a data processing agreement and a review of where the data is stored.
- All sample data is synthetic. Parties are marked "(Demo)" and the GSTINs are built from fake PANs.
