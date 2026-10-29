"""End-to-end pipeline: PDFs -> parse -> validate -> CSV / JSON / exceptions report."""
from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .exporters import export_tally_xml, export_zoho_csv, is_intra, line_rate, line_tax, load_mapping
from .gstin import STATE_CODES
from .llm import LLMAdapter, get_adapter, usage_records, write_usage_log
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
    intra = is_intra(inv)
    rows = []
    for li in inv.line_items:
        cgst, sgst, igst = line_tax(inv, li)  # per-line rate when printed, else pro-rated invoice tax
        rate = line_rate(inv, li)
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
            "Tax Name": ("GST" if intra else "IGST") + f"{rate:g}",
            "Tax Percentage": f"{rate:g}",
            "CGST": f"{cgst:.2f}",
            "SGST": f"{sgst:.2f}",
            "IGST": f"{igst:.2f}",
            "Bill Total": f"{inv.grand_total or 0:.2f}",
            "Status": res.status,
            "Review Flag": "Y" if res.needs_human_review else "N",
        })
    return rows


def run(input_dir: Path, output_dir: Path, review_threshold: float = 0.75,
        adapter: LLMAdapter | None = None, ocr: bool = False, mappings: dict[str, Path] | None = None) -> RunSummary:
    adapter = adapter or get_adapter()
    log.info("LLM mode: %s", adapter.name if adapter.available else "offline (deterministic regex parser)")
    output_dir.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    results: list[tuple[Invoice, ValidationResult]] = []
    for pdf in sorted(input_dir.glob("*.pdf")):
        inv = parse(pdf, adapter, ocr)
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

    outputs = {"csv": str(csv_path)}
    for key, filename, exporter in (("tally", "tally_purchase_vouchers.xml", export_tally_xml),
                                    ("zoho", "zoho_bills.csv", export_zoho_csv)):
        if mappings and key in mappings:
            n = exporter(results, load_mapping(mappings[key]), output_dir / filename)
            outputs[key] = str(output_dir / filename)
            log.info("%s export: %d record(s) -> %s", key, n, filename)

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

    outputs.update({"json": str(json_path), "exceptions": str(exc_path)})
    if usage_records():  # online run: tokens, latency and estimated cost per call
        write_usage_log(output_dir / "llm_usage.json")
        outputs["llm_usage"] = str(output_dir / "llm_usage.json")
    statuses = [r.status for _, r in results]
    return RunSummary(len(results), statuses.count("OK"), statuses.count("REVIEW"), statuses.count("EXCEPTION"),
                      outputs)
