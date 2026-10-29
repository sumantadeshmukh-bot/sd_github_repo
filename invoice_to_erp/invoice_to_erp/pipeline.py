"""End-to-end pipeline: PDFs -> parse -> validate -> CSV / JSON / exceptions report."""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .gstin import STATE_CODES
from .llm import LLMAdapter, get_adapter
from .models import Invoice, ValidationResult
from .parse import dump, parse
from .validate import validate

log = logging.getLogger(__name__)

# Column layout modelled on common purchase-bill import templates (Zoho Books-style
# "bills" import; Tally users typically map these via an import utility). Verify
# against the exact template/version the client uses before go-live.
CSV_COLUMNS = [
    "Bill Date", "Bill Number", "Vendor Name", "GST Treatment", "GSTIN", "Source of Supply",
    "Destination of Supply", "Item Name", "HSN/SAC", "Quantity", "Rate", "Item Total",
    "Tax Name", "Tax Percentage", "CGST", "SGST", "IGST", "Bill Total", "Status", "Review Flag",
]


@dataclass
class RunSummary:
    total: int
    ok: int
    review: int
    exceptions: int
    outputs: dict[str, str]


def _csv_rows(inv: Invoice, res: ValidationResult) -> list[dict[str, str]]:
    intra = bool(inv.supplier_gstin and inv.place_of_supply and inv.supplier_gstin[:2] == inv.place_of_supply)
    rows = []
    for li in inv.line_items:
        share = (li.amount / inv.taxable_value) if inv.taxable_value else 0  # pro-rate tax per line
        rows.append({
            "Bill Date": inv.invoice_date.strftime("%d/%m/%Y") if inv.invoice_date else "",
            "Bill Number": inv.invoice_no or "",
            "Vendor Name": inv.supplier_name or "",
            "GST Treatment": "business_gst",
            "GSTIN": inv.supplier_gstin or "",
            "Source of Supply": STATE_CODES.get((inv.supplier_gstin or "  ")[:2], ""),
            "Destination of Supply": STATE_CODES.get(inv.place_of_supply or "", ""),
            "Item Name": li.description,
            "HSN/SAC": li.hsn_sac,
            "Quantity": f"{li.quantity}",
            "Rate": f"{li.rate:.2f}",
            "Item Total": f"{li.amount:.2f}",
            "Tax Name": ("GST" if intra else "IGST") + f"{inv.tax_rate_pct or 0:g}",
            "Tax Percentage": f"{inv.tax_rate_pct or 0:g}",
            "CGST": f"{inv.cgst * share:.2f}",
            "SGST": f"{inv.sgst * share:.2f}",
            "IGST": f"{inv.igst * share:.2f}",
            "Bill Total": f"{inv.grand_total or 0:.2f}",
            "Status": res.status,
            "Review Flag": "Y" if res.needs_human_review else "N",
        })
    return rows


def run(input_dir: Path, output_dir: Path, review_threshold: float = 0.75,
        adapter: LLMAdapter | None = None) -> RunSummary:
    adapter = adapter or get_adapter()
    log.info("LLM mode: %s", adapter.name if adapter.available else "offline (deterministic regex parser)")
    output_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    results: list[tuple[Invoice, ValidationResult]] = []
    for pdf in sorted(input_dir.glob("*.pdf")):
        inv = parse(pdf, adapter)
        res = validate(inv, seen, review_threshold)
        log.info("%-26s %-18s %s", pdf.name, inv.invoice_no, res.status)
        results.append((inv, res))

    csv_path = output_dir / "erp_import_bills.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for inv, res in results:
            if res.status != "EXCEPTION":  # only clean/reviewable bills go to the import file
                w.writerows(_csv_rows(inv, res))

    json_path = output_dir / "invoices.json"
    json_path.write_text(json.dumps(
        [{"invoice": dump(inv), "validation": {**res.model_dump(), "status": res.status}} for inv, res in results],
        indent=2, default=str), encoding="utf-8")

    exc_path = output_dir / "exceptions_report.md"
    lines = ["# Invoice exceptions report", "",
             f"Processed {len(results)} invoices. Exceptions are held back from the ERP import file.", "",
             "| File | Invoice No | Status | Reasons |", "|---|---|---|---|"]
    for inv, res in results:
        if res.status != "OK":
            reasons = "<br>".join(res.errors + res.review_reasons + res.warnings)
            lines.append(f"| {inv.source_file} | {inv.invoice_no} | {res.status} | {reasons} |")
    exc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    statuses = [r.status for _, r in results]
    return RunSummary(len(results), statuses.count("OK"), statuses.count("REVIEW"), statuses.count("EXCEPTION"),
                      {"csv": str(csv_path), "json": str(json_path), "exceptions": str(exc_path)})
