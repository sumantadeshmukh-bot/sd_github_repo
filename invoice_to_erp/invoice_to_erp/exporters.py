"""ERP export formats: Tally Prime purchase vouchers (XML) and Zoho Books bills (CSV).

Both are driven by a mapping file in ``mappings/`` so ledger names, tags and column
headers can be changed without touching code. The mappings are ASSUMPTIONS modelled on
common templates; each file says so and the README lists what to verify with the client.
"""
from __future__ import annotations

import csv
import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
from typing import Any

from .gstin import STATE_CODES
from .models import Invoice, LineItem, ValidationResult

TWO = Decimal("0.01")


def is_intra(inv: Invoice) -> bool:
    return bool(inv.supplier_gstin and inv.place_of_supply and inv.supplier_gstin[:2] == inv.place_of_supply)


def line_rate(inv: Invoice, li: LineItem) -> Decimal:
    """Effective GST rate for a line: its own rate if printed, else the invoice's single rate."""
    return li.tax_rate_pct if li.tax_rate_pct is not None else (inv.tax_rate_pct or Decimal("0"))


def line_tax(inv: Invoice, li: LineItem) -> tuple[Decimal, Decimal, Decimal]:
    """(CGST, SGST, IGST) for one line. Per-line rate when printed, else pro-rata of the invoice tax."""
    if li.tax_rate_pct is not None:
        tax = li.amount * li.tax_rate_pct / 100
        return ((tax / 2, tax / 2, Decimal("0")) if is_intra(inv) else (Decimal("0"), Decimal("0"), tax))
    share = (li.amount / inv.taxable_value) if inv.taxable_value else Decimal("0")
    return inv.cgst * share, inv.sgst * share, inv.igst * share


def slab_taxes(inv: Invoice) -> dict[Decimal, dict[str, Decimal]]:
    """effective GST rate -> {kind: amount} as printed on the invoice (one slab for single-rate bills)."""
    slabs: dict[Decimal, dict[str, Decimal]] = {}
    if inv.tax_lines:
        for tl in inv.tax_lines:
            d = slabs.setdefault(tl.effective_gst_pct, {})
            d[tl.kind] = d.get(tl.kind, Decimal("0")) + tl.amount
    elif inv.tax_rate_pct is not None:
        slabs[inv.tax_rate_pct] = {k: v for k, v in (("CGST", inv.cgst), ("SGST", inv.sgst), ("IGST", inv.igst)) if v}
    return slabs


def taxable_by_rate(inv: Invoice) -> dict[Decimal, Decimal]:
    out: dict[Decimal, Decimal] = {}
    for li in inv.line_items:
        r = line_rate(inv, li)
        out[r] = out.get(r, Decimal("0")) + li.amount
    return out


def _fmt_rate(r: Decimal) -> str:
    return f"{r.normalize():f}"


# ---------------------------------------------------------------- Tally Prime
def export_tally_xml(items: list[tuple[Invoice, ValidationResult]], mapping: dict[str, Any], path: Path) -> int:
    """Write purchase vouchers for non-exception invoices. Returns the number of vouchers."""
    tag = mapping["ledger_entry_tag"]
    env = ET.Element("ENVELOPE")
    hdr = ET.SubElement(env, "HEADER")
    ET.SubElement(hdr, "TALLYREQUEST").text = "Import Data"
    imp = ET.SubElement(ET.SubElement(env, "BODY"), "IMPORTDATA")
    desc = ET.SubElement(imp, "REQUESTDESC")
    ET.SubElement(desc, "REPORTNAME").text = "Vouchers"
    ET.SubElement(ET.SubElement(desc, "STATICVARIABLES"), "SVCURRENTCOMPANY").text = mapping["company_name"]
    data = ET.SubElement(imp, "REQUESTDATA")
    count = 0
    for inv, res in items:
        if res.status == "EXCEPTION":
            continue
        count += 1
        msg = ET.SubElement(data, "TALLYMESSAGE", {"xmlns:UDF": "TallyUDF"})
        v = ET.SubElement(msg, "VOUCHER", {"VCHTYPE": mapping["voucher_type"], "ACTION": "Create",
                                           "OBJVIEW": "Accounting Voucher View"})
        ET.SubElement(v, "DATE").text = inv.invoice_date.strftime(mapping["date_format"]) if inv.invoice_date else ""
        ET.SubElement(v, "VOUCHERTYPENAME").text = mapping["voucher_type"]
        ET.SubElement(v, "VOUCHERNUMBER").text = inv.invoice_no or ""
        ET.SubElement(v, "REFERENCE").text = inv.invoice_no or ""
        party = mapping["party_ledger"].format(vendor_name=inv.supplier_name or "")
        ET.SubElement(v, "PARTYLEDGERNAME").text = party
        if mapping.get("include_gst_fields"):
            ET.SubElement(v, "PARTYGSTIN").text = inv.supplier_gstin or ""
            ET.SubElement(v, "PLACEOFSUPPLY").text = STATE_CODES.get(inv.place_of_supply or "", "")
        ET.SubElement(v, "NARRATION").text = mapping["narration"].format(source_file=inv.source_file, status=res.status)

        def entry(name: str, amount: Decimal, debit: bool) -> None:
            e = ET.SubElement(v, tag)
            ET.SubElement(e, "LEDGERNAME").text = name
            ET.SubElement(e, "ISDEEMEDPOSITIVE").text = "Yes" if debit else "No"
            ET.SubElement(e, "AMOUNT").text = f"{(-amount if debit else amount).quantize(TWO)}"

        total = inv.grand_total or Decimal("0")
        entry(party, total, debit=False)  # party ledger credited
        booked = Decimal("0")
        for rate, taxable in sorted(taxable_by_rate(inv).items()):
            entry(mapping["purchase_ledger"].format(rate=_fmt_rate(rate)), taxable, debit=True)
            booked += taxable
        slabs = slab_taxes(inv)
        for rate in sorted(slabs):
            for kind, amt in slabs[rate].items():
                comp = rate / 2 if kind in ("CGST", "SGST") else rate
                entry(mapping["tax_ledgers"][kind].format(rate=_fmt_rate(comp)), amt, debit=True)
                booked += amt
        diff = (total - booked).quantize(TWO)
        if diff:  # keep the voucher balanced: book the round-off to its own ledger
            entry(mapping["round_off_ledger"], abs(diff), debit=diff > 0)
    ET.indent(env)
    path.write_text(ET.tostring(env, encoding="unicode", xml_declaration=False) + "\n", encoding="utf-8")
    return count


# ---------------------------------------------------------------- Zoho Books
def zoho_rows(items: list[tuple[Invoice, ValidationResult]]) -> list[dict[str, Any]]:
    """Canonical per-line fields that a Zoho column mapping can pick from."""
    rows = []
    for inv, res in items:
        if res.status == "EXCEPTION":
            continue
        for li in inv.line_items:
            rate = line_rate(inv, li)
            rows.append({
                "bill_date": inv.invoice_date, "bill_number": inv.invoice_no or "",
                "vendor_name": inv.supplier_name or "", "gstin": inv.supplier_gstin or "",
                "source_of_supply": STATE_CODES.get((inv.supplier_gstin or "  ")[:2], ""),
                "destination_of_supply": STATE_CODES.get(inv.place_of_supply or "", ""),
                "item_name": li.description, "hsn_sac": li.hsn_sac, "quantity": f"{li.quantity}",
                "rate": f"{li.rate:.2f}", "item_total": f"{li.amount:.2f}",
                "tax_name": ("GST" if is_intra(inv) else "IGST") + _fmt_rate(rate),
                "tax_percentage": _fmt_rate(rate),
                "review_note": "; ".join(res.review_reasons) if res.needs_human_review else "",
            })
    return rows


def export_zoho_csv(items: list[tuple[Invoice, ValidationResult]], mapping: dict[str, Any], path: Path) -> int:
    rows = zoho_rows(items)
    cols = mapping["columns"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([c["column"] for c in cols])
        for r in rows:
            out = []
            for c in cols:
                if "const" in c:
                    out.append(c["const"])
                    continue
                val = r.get(c["field"], "")
                if c.get("format") == "date" and val:
                    val = val.strftime(mapping["date_format"])
                out.append(val)
            w.writerow(out)
    return len(rows)


def load_mapping(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
