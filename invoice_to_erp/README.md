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
Outputs go to `output/`: `erp_import_bills.csv`, `invoices.json` and `exceptions_report.md`. The sample PDFs are in `data/invoices/`.

## Switching to Gemini or Claude
```bash
export ANTHROPIC_API_KEY=...        # or: export GEMINI_API_KEY=...
export LLM_PROVIDER=claude          # optional: force a provider (claude|gemini|offline)
export ANTHROPIC_MODEL=...          # optional: override the default model name
```
In online mode the LLM returns JSON that is validated against the same pydantic schema. The key fields (invoice no., GSTINs, taxable value, total) are then checked against the regex parse. If the two disagree, that field's confidence drops to 0.5, which triggers human review. If the LLM fails or returns invalid output, the regex result is used. The online adapters use only the standard library and are **untested here because no API key was available**.

## Limitations
- The offline parser is tuned to the three demo layouts. Real vendors need layout templates or the LLM mode, plus a labelled test set.
- It handles text PDFs only. Scanned bills need OCR (for example, Tesseract or a cloud OCR service).
- It assumes one GST rate per invoice. It does not check the HSN code against the rate schedule (GST rates changed from 22 Sep 2025, so keep rates in config).
- Duplicate detection only covers the current batch. In production, check against the ERP or a database.
- The CSV columns follow a Zoho Books-style bills import. Map them to the client's exact Zoho or Tally template and version before go-live (Tally usually imports through XML or an import utility).

## Compliance notes (India)
- Invoice particulars follow **CGST Act s.31 and CGST Rules 2017, Rule 46** (GSTIN, an invoice number unique per financial year, HSN/SAC, taxable value, rate, place of supply).
- Intra-state vs inter-state logic follows **IGST Act ss.7-8**, with place of supply under ss.10-12. This demo uses a simplified version.
- **E-invoicing (IRN/QR)** is mandatory above Rs. 5 crore aggregate turnover. This demo does not verify the IRN, so verify the signed QR through the IRP in production.
- Input tax credit eligibility depends on **GSTR-2B** reconciliation, which this demo does not cover.
- Bills from sole proprietors can contain personal data covered by the DPDP Act 2023. Offline mode keeps all processing on the machine. Online mode sends invoice text to the LLM provider, so you need a data processing agreement and a review of where the data is stored.
- All sample data is synthetic. Parties are marked "(Demo)" and the GSTINs are built from fake PANs.
