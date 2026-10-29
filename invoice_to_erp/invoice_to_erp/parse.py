"""Invoice text extraction and parsing.

Offline mode: label-driven regex + heuristics with a per-field confidence score.
Online mode: ask the LLM for structured JSON matching the pydantic schema, then
cross-check key fields against the regex parse; disagreements lower confidence
so a human reviews them (the LLM never silently overrides deterministic checks).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber
from pydantic import ValidationError

from .gstin import STATE_BY_NAME, STATE_CODES
from .llm import LLMAdapter, OfflineAdapter
from .models import Invoice, LineItem

log = logging.getLogger(__name__)

AMT = r"([\d,]+\.\d{2})"
GSTIN_TOKEN = r"\b(\d{2}[A-Z0-9]{13})\b"
DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y")
LINE_RE = re.compile(
    r"^(?:\d{1,3}\s+)?(?P<desc>[A-Za-z].*?)\s+(?P<hsn>\d{4,8})\s+(?P<qty>\d+(?:\.\d+)?)\s+"
    + r"(?P<rate>[\d,]+\.\d{2})\s+(?P<amt>[\d,]+\.\d{2})$"
)
HEADER_WORDS = {"Description", "Item", "Sl"}


def extract_text(pdf_path: Path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _parse_date(s: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _search(patterns: list[tuple[str, float]], text: str) -> tuple[str | None, float]:
    """Try patterns in priority order; return (group 1, confidence of that pattern)."""
    for pat, conf in patterns:
        m = re.search(pat, text, flags=re.MULTILINE | re.IGNORECASE)
        if m:
            return m.group(1).strip(), conf
    return None, 0.0


def parse_offline(text: str, source_file: str) -> Invoice:
    conf: dict[str, float] = {}
    inv = Invoice(source_file=source_file)

    inv.invoice_no, conf["invoice_no"] = _search(
        [(r"(?:Invoice\s*No\.?|Invoice\s*#|Invoice\s*Number|Bill\s*No\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9/\-]+)", 1.0),
         (r"^INVOICE\s+([A-Z0-9][A-Z0-9/\-]+)", 0.8)], text)

    date_txt, conf["invoice_date"] = _search(
        [(r"(?:Invoice\s*Date|Dated|^Date)\s*:?\s*(\d[\w\-/. ]{6,10}\d)", 1.0),
         (r"(\d{2}[/\-.]\d{2}[/\-.]\d{4}|\d{4}-\d{2}-\d{2}|\d{2}-[A-Za-z]{3}-\d{4})", 0.8)], text)
    inv.invoice_date = _parse_date(date_txt) if date_txt else None
    if date_txt and not inv.invoice_date:
        conf["invoice_date"] = 0.3

    # GSTINs: explicit role labels first; otherwise position relative to "Bill To".
    sup, c_sup = _search([(r"(?:Supplier\s*GSTIN|GSTIN\s*\(Seller\))\s*:?\s*" + GSTIN_TOKEN, 1.0)], text)
    buy, c_buy = _search([(r"(?:Customer\s*GSTIN|Buyer\s*GSTIN|GSTIN\s*\(Buyer\))\s*:?\s*" + GSTIN_TOKEN, 1.0)], text)
    if not (sup and buy):
        split = re.search(r"Bill\s*To|Buyer|Customer|Ship\s*To", text, re.IGNORECASE)
        head, tail = (text[: split.start()], text[split.start():]) if split else (text, "")
        if not sup:
            m = re.search(r"GSTIN\s*:?\s*" + GSTIN_TOKEN, head)
            sup, c_sup = (m.group(1), 0.9) if m else (None, 0.0)
        if not buy and tail:
            m = re.search(r"GSTIN\s*:?\s*" + GSTIN_TOKEN, tail)
            buy, c_buy = (m.group(1), 0.9) if m else (None, 0.0)
    inv.supplier_gstin, conf["supplier_gstin"] = sup, c_sup
    inv.buyer_gstin, conf["buyer_gstin"] = buy, c_buy

    name, c = _search([(r"^(?:Supplier|Seller|From)\s*:\s*([^,\n]+)", 1.0)], text)
    if not name:  # heuristic: first line of the document is usually the supplier letterhead
        first = text.strip().splitlines()[0] if text.strip() else ""
        name, c = (first, 0.8) if re.search(r"(Ltd|LLP|Co\b|Supplies|Services|\(Demo\))", first) else (None, 0.0)
    inv.supplier_name, conf["supplier_name"] = name, c
    inv.buyer_name, conf["buyer_name"] = _search(
        [(r"^(?:Customer|Buyer)\s*:\s*([^,\n]+)", 1.0), (r"Bill\s*To\s*:?\s*\n([^\n]+)", 1.0)], text)

    pos_txt, c_pos = _search([(r"Place\s*of\s*Supply\s*:?\s*([^\n]+)", 1.0)], text)
    pos = None
    if pos_txt:
        m = re.search(r"\b(\d{2})\b", pos_txt)
        pos = m.group(1) if m else STATE_BY_NAME.get(pos_txt.strip().lower())
    if not pos and inv.buyer_gstin:  # infer from buyer GSTIN -> low confidence, human review
        pos, c_pos = inv.buyer_gstin[:2], 0.6
    inv.place_of_supply, conf["place_of_supply"] = (pos, c_pos) if pos in STATE_CODES else (pos, min(c_pos, 0.4))

    for raw in text.splitlines():
        m = LINE_RE.match(raw.strip())
        if m and m.group("desc").split()[0] not in HEADER_WORDS:
            inv.line_items.append(LineItem(description=m["desc"], hsn_sac=m["hsn"], quantity=_dec(m["qty"]),
                                           rate=_dec(m["rate"]), amount=_dec(m["amt"])))
    conf["line_items"] = 1.0 if inv.line_items else 0.0

    taxable, conf["taxable_value"] = _search(
        [(r"(?:Taxable\s*Value|Sub\s*Total|Total\s*Before\s*Tax)[^\d\n]*" + AMT, 1.0)], text)
    inv.taxable_value = _dec(taxable) if taxable else None
    for tax in ("cgst", "sgst", "igst"):
        m = re.search(tax + r"[^\d\n]*(\d+(?:\.\d+)?)\s*%[^\d\n]*" + AMT, text, re.IGNORECASE)
        if m:
            setattr(inv, tax, _dec(m.group(2)))
            rate = _dec(m.group(1))
            inv.tax_rate_pct = rate * 2 if tax in ("cgst", "sgst") else rate
    conf["tax"] = 1.0 if inv.tax_rate_pct is not None else 0.0
    total, conf["grand_total"] = _search(
        [(r"(?:Grand\s*Total|Total\s*Amount\s*Payable|Invoice\s*Total|Amount\s*Payable)[^\d\n]*" + AMT, 1.0)], text)
    inv.grand_total = _dec(total) if total else None

    inv.confidence = conf
    return inv


LLM_SYSTEM = (
    "You extract Indian GST invoice data. Use only text present in the invoice. "
    "Return JSON with keys: invoice_no, invoice_date (YYYY-MM-DD), supplier_name, supplier_gstin, "
    "buyer_name, buyer_gstin, place_of_supply (2-digit state code), line_items "
    "[{description, hsn_sac, quantity, rate, amount}], taxable_value, cgst, sgst, igst, "
    "tax_rate_pct, grand_total. Use null for anything not present; never guess."
)
CROSS_CHECK = ("invoice_no", "supplier_gstin", "buyer_gstin", "grand_total", "taxable_value")


def parse(pdf_path: Path, adapter: LLMAdapter | None = None) -> Invoice:
    """Parse one PDF. Uses the LLM when available, always cross-checked by regex."""
    adapter = adapter or OfflineAdapter()
    text = extract_text(pdf_path)
    baseline = parse_offline(text, pdf_path.name)
    if not adapter.available:
        return baseline
    data = adapter.complete_json(LLM_SYSTEM, text)
    if not data:
        return baseline
    try:
        llm_inv = Invoice(source_file=pdf_path.name, parser=f"llm-{adapter.name}", **data)
    except (ValidationError, TypeError) as exc:
        log.warning("LLM output failed schema validation for %s: %s", pdf_path.name, exc)
        return baseline
    conf = {k: 0.9 for k in baseline.confidence}
    for f in CROSS_CHECK:
        if getattr(llm_inv, f) != getattr(baseline, f):
            conf[f] = 0.5  # LLM and regex disagree -> human review
    llm_inv.confidence = conf
    return llm_inv


def dump(inv: Invoice) -> dict:
    return json.loads(inv.model_dump_json())
