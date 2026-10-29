"""Business-rule validations for parsed GST invoices."""
from __future__ import annotations

import re
from decimal import Decimal

from .gstin import STATE_CODES, validate_gstin
from .models import Invoice, ValidationResult

LINE_TOL = Decimal("0.01")  # per-line rounding tolerance
TOTAL_TOL = Decimal("1.00")  # invoice-level round-off allowed (Rs. 1)


def _fy(inv: Invoice) -> str:
    """Indian financial year (Apr-Mar) of the invoice date, e.g. 2026-27."""
    if not inv.invoice_date:
        return "unknown"
    y = inv.invoice_date.year if inv.invoice_date.month >= 4 else inv.invoice_date.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def duplicate_key(inv: Invoice) -> str:
    """Supplier GSTIN + normalised invoice number + FY (GST invoice numbers are unique per FY)."""
    norm = re.sub(r"[^A-Z0-9]", "", (inv.invoice_no or "").upper())
    return f"{(inv.supplier_gstin or '').upper()}|{norm}|{_fy(inv)}"


def validate(inv: Invoice, seen: dict[str, str], review_threshold: float) -> ValidationResult:
    """Validate one invoice. ``seen`` maps duplicate keys -> first source file (mutated)."""
    r = ValidationResult(invoice_no=inv.invoice_no, source_file=inv.source_file)

    # 1. Mandatory fields
    for f in ("invoice_no", "invoice_date", "supplier_gstin", "buyer_gstin", "taxable_value", "grand_total"):
        if getattr(inv, f) in (None, ""):
            r.errors.append(f"Mandatory field '{f}' not found")
    if not inv.line_items:
        r.errors.append("No line items found")

    # 2. GSTIN format + mod-36 checksum
    for role, g in (("Supplier", inv.supplier_gstin), ("Buyer", inv.buyer_gstin)):
        if g:
            r.errors.extend(f"{role}: {p}" for p in validate_gstin(g))

    # 3. Place of supply vs tax type (IGST Act s.7/8 logic, simplified)
    if inv.supplier_gstin and inv.place_of_supply:
        intra = inv.supplier_gstin[:2] == inv.place_of_supply
        has_cg = inv.cgst > 0 or inv.sgst > 0
        if intra and inv.igst > 0:
            r.errors.append(f"Intra-state supply (state {inv.place_of_supply}) charged IGST; expected CGST+SGST")
        if not intra and has_cg:
            r.errors.append(
                f"Inter-state supply ({inv.supplier_gstin[:2]} -> {inv.place_of_supply}) charged CGST/SGST; expected IGST")
        if intra and inv.cgst != inv.sgst:
            r.errors.append(f"CGST {inv.cgst} != SGST {inv.sgst}")
    if inv.place_of_supply and inv.place_of_supply not in STATE_CODES:
        r.warnings.append(f"Place of supply '{inv.place_of_supply}' not in known state list")

    # 4. Arithmetic: lines, taxable value, tax, grand total
    for i, li in enumerate(inv.line_items, 1):
        expected = (li.quantity * li.rate).quantize(Decimal("0.01"))
        if abs(expected - li.amount) > LINE_TOL:
            r.errors.append(f"Line {i} '{li.description}': {li.quantity} x {li.rate} = {expected}, invoice shows {li.amount}")
    if inv.taxable_value is not None and inv.line_items:
        s = sum((li.amount for li in inv.line_items), Decimal("0"))
        if abs(s - inv.taxable_value) > LINE_TOL:
            r.errors.append(f"Sum of line amounts {s} != taxable value {inv.taxable_value}")
    tax_total = inv.cgst + inv.sgst + inv.igst
    if inv.taxable_value is not None and inv.tax_rate_pct is not None:
        exp_tax = (inv.taxable_value * inv.tax_rate_pct / 100).quantize(Decimal("0.01"))
        if abs(exp_tax - tax_total) > TOTAL_TOL:
            r.errors.append(f"Tax {tax_total} != {inv.tax_rate_pct}% of {inv.taxable_value} ({exp_tax})")
    if inv.taxable_value is not None and inv.grand_total is not None:
        exp_total = inv.taxable_value + tax_total
        if abs(exp_total - inv.grand_total) > TOTAL_TOL:
            r.errors.append(f"Grand total {inv.grand_total} != taxable {inv.taxable_value} + tax {tax_total} ({exp_total})")

    # 5. Duplicate detection within the batch (swap ``seen`` for a DB lookup in production)
    key = duplicate_key(inv)
    if inv.invoice_no and inv.supplier_gstin:
        if key in seen:
            r.errors.append(f"Duplicate of {seen[key]} (same supplier GSTIN + invoice no + FY {_fy(inv)})")
        else:
            seen[key] = inv.source_file

    # 6. Human review for low-confidence fields
    low = {k: v for k, v in inv.confidence.items() if v < review_threshold}
    if low:
        r.needs_human_review = True
        r.review_reasons = [f"Low confidence on '{k}' ({v:.2f})" for k, v in sorted(low.items())]
    return r
