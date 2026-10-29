"""Generate 5 synthetic Indian GST purchase invoices as PDFs (three layouts).

All parties are fictional ("(Demo)" suffix) and every PDF carries a
"SYNTHETIC SAMPLE" footer. GSTINs are built from fake PANs with a correct
check digit, except invoice 4 which deliberately carries a bad check digit.

Scenarios:
    1  intra-state (KA->KA) CGST+SGST, clean                     layout A
    2  inter-state (MH->KA) IGST, services (SAC), clean           layout B
    3  inter-state (TN->KA) IGST, arithmetic errors, no explicit
       place-of-supply line (forces a low-confidence inference)  layout C
    4  inter-state (DL->KA) IGST, supplier GSTIN bad checksum     layout A
    5  duplicate re-send of invoice 1 in a different layout       layout B
    6  intra-state (KA->KA) with three GST rates (5/12/18%) on one
       invoice, per-line rate column, clean                        layout D
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .gstin import STATE_CODES, make_gstin

log = logging.getLogger(__name__)
TWO = Decimal("0.01")


def inr(x: Decimal) -> str:
    """Format with Indian digit grouping, e.g. 1,23,456.00."""
    q = x.quantize(TWO, rounding=ROUND_HALF_UP)
    whole, frac = f"{q:.2f}".split(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{sign}{whole}.{frac}"


@dataclass
class Party:
    name: str
    address: str
    gstin: str

    @property
    def state(self) -> str:
        return self.gstin[:2]


@dataclass
class Spec:
    file: str
    layout: str
    invoice_no: str
    date_text: str
    supplier: Party
    buyer: Party
    rate_pct: Decimal
    items: list[tuple[str, str, Decimal, Decimal]]  # desc, hsn, qty, rate
    show_pos: bool = True
    # deliberate errors: index -> wrong amount; total override
    bad_line_amounts: dict[int, Decimal] = field(default_factory=dict)
    total_override: Decimal | None = None
    item_rates: list[Decimal] | None = None  # per-line GST rate (layout D, several rates on one invoice)


BUYER = Party("Lotus Leaf Retail Pvt Ltd (Demo)", "12 MG Road, Bengaluru 560001", make_gstin("29", "AAECL7788H"))
S1 = Party("Kaveri Precision Tools Pvt Ltd (Demo)", "Plot 7, Peenya Industrial Area, Bengaluru", make_gstin("29", "AAKCK1234M"))
S2 = Party("Sahyadri Cloud Services LLP (Demo)", "Unit 4, Baner Road, Pune", make_gstin("27", "AAQFS5678P"))
S3 = Party("Coromandel Packaging Co (Demo)", "SIDCO Estate, Guindy, Chennai", make_gstin("33", "AABFC9012Q"))
S5 = Party("Hoysala Stationers and Gifts (Demo)", "Avenue Road, Bengaluru", make_gstin("29", "AAHFH2345K"))
_good4 = make_gstin("07", "ABCPY3456R")
_bad_check = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"[("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ".index(_good4[-1]) + 7) % 36]
S4 = Party("Yamuna Office Supplies (Demo)", "Okhla Phase II, New Delhi", _good4[:-1] + _bad_check)

D = Decimal
SPECS: list[Spec] = [
    Spec("inv01_kaveri.pdf", "A", "KPT/2026-27/0142", "14-Oct-2026", S1, BUYER, D("18"),
         [("Cordless drill 18V", "8467", D("4"), D("6250.00")),
          ("HSS drill bit set", "8207", D("10"), D("899.00"))]),
    Spec("inv02_sahyadri.pdf", "B", "SCS-INV-0098", "2026-10-16", S2, BUYER, D("18"),
         [("Managed cloud hosting Oct 2026", "998315", D("1"), D("48000.00")),
          ("Backup and DR support", "998713", D("1"), D("12500.00"))]),
    Spec("inv03_coromandel.pdf", "C", "CPC-7781", "18/10/2026", S3, BUYER, D("18"),
         [("Corrugated boxes 5-ply", "4819", D("500"), D("42.50")),
          ("Kraft paper tape roll", "4823", D("120"), D("65.00"))],
         show_pos=False, bad_line_amounts={1: D("8500.00")}, total_override=D("35000.00")),
    Spec("inv04_yamuna.pdf", "A", "YOS/1029", "20-Oct-2026", S4, BUYER, D("18"),
         [("A4 copier paper 75gsm ream", "4802", D("60"), D("310.00")),
          ("Gel pens box of 10", "9608", D("25"), D("180.00"))]),
    Spec("inv05_kaveri_resend.pdf", "B", "KPT/2026-27/0142", "2026-10-14", S1, BUYER, D("18"),
         [("Cordless drill 18V", "8467", D("4"), D("6250.00")),
          ("HSS drill bit set", "8207", D("10"), D("899.00"))]),
    Spec("inv06_hoysala_multirate.pdf", "D", "HSG/0233", "22-Oct-2026", S5, BUYER, D("18"),
         [("Steel office cabinet", "9403", D("2"), D("14500.00")),
          ("A4 copier paper 75gsm ream", "4802", D("100"), D("310.00")),
          ("Printed notebooks", "4820", D("50"), D("120.00"))],
         item_rates=[D("18"), D("12"), D("5")]),
]


def _multi_rate_taxes(spec: Spec, amounts: list[Decimal]) -> list[tuple[str, Decimal, Decimal]]:
    """(kind, component rate, amount) per GST slab: CGST+SGST halves when intra-state, else IGST."""
    by_rate: dict[Decimal, Decimal] = {}
    for rate, amt in zip(spec.item_rates or [], amounts):
        by_rate[rate] = by_rate.get(rate, D("0")) + amt
    out: list[tuple[str, Decimal, Decimal]] = []
    for rate, taxable in sorted(by_rate.items()):
        if spec.supplier.state == spec.buyer.state:
            half = (taxable * rate / 2 / 100).quantize(TWO, rounding=ROUND_HALF_UP)
            out += [("CGST", rate / 2, half), ("SGST", rate / 2, half)]
        else:
            out.append(("IGST", rate, (taxable * rate / 100).quantize(TWO, rounding=ROUND_HALF_UP)))
    return out


def _compute(spec: Spec) -> tuple[list[Decimal], Decimal, dict[str, Decimal], Decimal]:
    amounts = [(q * r).quantize(TWO) for _, _, q, r in spec.items]
    for idx, wrong in spec.bad_line_amounts.items():
        amounts[idx] = wrong
    taxable = sum(amounts, D("0"))
    if spec.item_rates:
        taxes: dict[str, Decimal] = {}
        for kind, _, amt in _multi_rate_taxes(spec, amounts):
            taxes[kind] = taxes.get(kind, D("0")) + amt
    elif spec.supplier.state == spec.buyer.state:
        half = (taxable * spec.rate_pct / 2 / 100).quantize(TWO, rounding=ROUND_HALF_UP)
        taxes = {"CGST": half, "SGST": half}
    else:
        taxes = {"IGST": (taxable * spec.rate_pct / 100).quantize(TWO, rounding=ROUND_HALF_UP)}
    total = spec.total_override or taxable + sum(taxes.values(), D("0"))
    return amounts, taxable, taxes, total


def _footer(c: canvas.Canvas) -> None:
    c.setFont("Helvetica-Oblique", 7)
    c.drawString(40, 30, "SYNTHETIC SAMPLE FOR DEMONSTRATION ONLY - NOT A REAL INVOICE - all parties fictional")


def _draw(spec: Spec, path: Path) -> None:
    amounts, taxable, taxes, total = _compute(spec)
    pos = f"{STATE_CODES[spec.buyer.state]} ({spec.buyer.state})"
    c = canvas.Canvas(str(path), pagesize=A4)
    _, h = A4
    y = h - 50

    def line(text: str, x: float = 40, font: str = "Helvetica", size: int = 10, dy: int = 15) -> None:
        nonlocal y
        c.setFont(font, size)
        c.drawString(x, y, text)
        y -= dy

    def row(cols: list[tuple[float, str]], font: str = "Helvetica", size: int = 9) -> None:
        nonlocal y
        c.setFont(font, size)
        for x, t in cols:
            c.drawString(x, y, t)
        y -= 14

    if spec.layout in ("A", "D"):  # classic: supplier on top, "Bill To" block (D adds a per-line GST % column)
        line(spec.supplier.name, font="Helvetica-Bold", size=13, dy=16)
        line(spec.supplier.address, size=9)
        line(f"GSTIN: {spec.supplier.gstin}", size=9, dy=22)
        line("TAX INVOICE", x=250, font="Helvetica-Bold", size=14, dy=22)
        line(f"Invoice No: {spec.invoice_no}")
        line(f"Invoice Date: {spec.date_text}", dy=20)
        line("Bill To:", font="Helvetica-Bold")
        line(spec.buyer.name)
        line(spec.buyer.address, size=9)
        line(f"GSTIN: {spec.buyer.gstin}", size=9)
        line(f"Place of Supply: {pos}", dy=24)
        multi = spec.item_rates is not None
        cols = [40, 65, 240, 310, 350, 420, 505] if multi else [40, 70, 300, 370, 420, 500]
        heads = ["Sl", "Description", "HSN/SAC", "Qty", "Rate", "Amount"] + (["GST%"] if multi else [])
        row(list(zip(cols, heads)), font="Helvetica-Bold")
        for i, ((d, hsn, q, r), amt) in enumerate(zip(spec.items, amounts), 1):
            cells = [str(i), d, hsn, f"{q.normalize():f}", inr(r), inr(amt)]
            if multi:
                cells.append(f"{spec.item_rates[i - 1].normalize():f}%")
            row(list(zip(cols, cells)))
        y -= 10
        line(f"Taxable Value   {inr(taxable)}", x=350)
        if multi:
            for k, comp_rate, v in _multi_rate_taxes(spec, amounts):
                line(f"{k} @ {comp_rate.normalize():f}%   {inr(v)}", x=350)
        else:
            for k, v in taxes.items():
                rate = spec.rate_pct / (2 if k != "IGST" else 1)
                line(f"{k} @ {rate.normalize():f}%   {inr(v)}", x=350)
        line(f"Grand Total   INR {inr(total)}", x=350, font="Helvetica-Bold")
    elif spec.layout == "B":  # modern: header band, labelled parties, table w/o serial
        c.setFillColorRGB(0.12, 0.3, 0.55)
        c.rect(0, h - 70, A4[0], 70, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(40, h - 45, "Tax Invoice")
        c.setFillColorRGB(0, 0, 0)
        y = h - 95
        line(f"Supplier: {spec.supplier.name}", font="Helvetica-Bold")
        line(f"Supplier GSTIN {spec.supplier.gstin}", size=9, dy=20)
        line(f"Customer: {spec.buyer.name}", font="Helvetica-Bold")
        line(f"Customer GSTIN {spec.buyer.gstin}", size=9, dy=20)
        line(f"Invoice # {spec.invoice_no}")
        line(f"Dated {spec.date_text}")
        line(f"Place of Supply {spec.buyer.state}-{STATE_CODES[spec.buyer.state]}", dy=24)
        cols = [40, 290, 360, 420, 500]
        row(list(zip(cols, ["Description", "HSN/SAC", "Qty", "Unit Price", "Line Total"])), font="Helvetica-Bold")
        for (d, hsn, q, r), amt in zip(spec.items, amounts):
            row(list(zip(cols, [d, hsn, f"{q.normalize():f}", inr(r), inr(amt)])))
        y -= 10
        line(f"Sub Total {inr(taxable)}", x=360)
        for k, v in taxes.items():
            rate = spec.rate_pct / (2 if k != "IGST" else 1)
            line(f"{k} ({rate.normalize():f}%) {inr(v)}", x=360)
        line(f"Total Amount Payable Rs. {inr(total)}", x=320, font="Helvetica-Bold")
    else:  # "C" compact: seller/buyer labels in brackets, no place-of-supply line
        line(f"INVOICE  {spec.invoice_no}   |   {spec.date_text}", font="Helvetica-Bold", size=12, dy=20)
        line(f"Seller: {spec.supplier.name}, {spec.supplier.address}", size=9)
        line(f"GSTIN (Seller): {spec.supplier.gstin}", size=9, dy=18)
        line(f"Buyer: {spec.buyer.name}, {spec.buyer.address}", size=9)
        line(f"GSTIN (Buyer): {spec.buyer.gstin}", size=9, dy=22)
        cols = [40, 250, 320, 380, 460]
        row(list(zip(cols, ["Item", "HSN", "Qty", "Rate", "Total"])), font="Helvetica-Bold")
        for (d, hsn, q, r), amt in zip(spec.items, amounts):
            row(list(zip(cols, [d, hsn, f"{q.normalize():f}", inr(r), inr(amt)])))
        y -= 10
        line(f"Total Before Tax: {inr(taxable)}", x=330)
        for k, v in taxes.items():
            rate = spec.rate_pct / (2 if k != "IGST" else 1)
            line(f"Add: {k} {rate.normalize():f}% : {inr(v)}", x=330)
        line(f"Invoice Total: {inr(total)}", x=330, font="Helvetica-Bold")
    _footer(c)
    c.save()


def generate(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in SPECS:
        p = out_dir / spec.file
        _draw(spec, p)
        paths.append(p)
    log.info("Generated %d synthetic invoices in %s", len(paths), out_dir)
    return paths
