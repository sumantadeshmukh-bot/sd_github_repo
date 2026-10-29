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
import shutil
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber
from pydantic import ValidationError

from .gstin import STATE_BY_NAME, STATE_CODES
from .llm import LLMAdapter, OfflineAdapter
from .models import Invoice, LineItem, TaxLine

log = logging.getLogger(__name__)

AMT = r"([\d,]+\.\d{2})"
GSTIN_TOKEN = r"\b(\d{2}[A-Z0-9]{13})\b"
DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y")
LINE_RE = re.compile(
    r"^(?:\d{1,3}\s+)?(?P<desc>[A-Za-z].*?)\s+(?P<hsn>\d{4,8})\s+(?P<qty>\d+(?:\.\d+)?)\s+"
    + r"(?P<rate>[\d,]+\.\d{2})\s+(?P<amt>[\d,]+\.\d{2})(?:\s+(?P<gst>\d+(?:\.\d+)?)\s*%)?$"
)
TAX_RE = re.compile(r"\b(cgst|sgst|igst)\b[^\d\n]*(\d+(?:\.\d+)?)\s*%[^\d\n]*" + AMT, re.IGNORECASE)
HEADER_WORDS = {"Description", "Item", "Sl"}


def ocr_available() -> bool:
    """True when pytesseract is importable and the tesseract binary is on PATH."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def extract_text(pdf_path: Path, ocr: bool = False) -> str:
    """Text layer via pdfplumber. With ``ocr=True``, pages with no text layer are OCR'd (if installed)."""
    with pdfplumber.open(pdf_path) as pdf:
        pages = []
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if ocr and not txt.strip():
                if ocr_available():
                    import pytesseract
                    txt = pytesseract.image_to_string(page.to_image(resolution=300).original)
                else:
                    log.warning("OCR requested for %s but pytesseract/tesseract is not installed; skipping OCR",
                                pdf_path.name)
            pages.append(txt)
        return "\n".join(pages)


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
                                           rate=_dec(m["rate"]), amount=_dec(m["amt"]),
                                           tax_rate_pct=_dec(m["gst"]) if m["gst"] else None))
    conf["line_items"] = 1.0 if inv.line_items else 0.0

    taxable, conf["taxable_value"] = _search(
        [(r"(?:Taxable\s*Value|Sub\s*Total|Total\s*Before\s*Tax)[^\d\n]*" + AMT, 1.0)], text)
    inv.taxable_value = _dec(taxable) if taxable else None
    for m in TAX_RE.finditer(text):  # one match per printed tax line (several rates -> several lines)
        inv.tax_lines.append(TaxLine(kind=m.group(1).upper(), rate_pct=_dec(m.group(2)), amount=_dec(m.group(3))))
    for tl in inv.tax_lines:
        setattr(inv, tl.kind.lower(), getattr(inv, tl.kind.lower()) + tl.amount)
    rates = {tl.effective_gst_pct for tl in inv.tax_lines}
    inv.tax_rate_pct = rates.pop() if len(rates) == 1 else None
    conf["tax"] = 1.0 if inv.tax_lines else 0.0
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
    "tax_rate_pct (null if lines have different rates; then set tax_rate_pct on each line item), "
    "tax_lines [{kind: CGST|SGST|IGST, rate_pct, amount}], grand_total. Use null for anything not present; never guess."
)
CROSS_CHECK = ("invoice_no", "supplier_gstin", "buyer_gstin", "grand_total", "taxable_value")


def parse(pdf_path: Path, adapter: LLMAdapter | None = None, ocr: bool = False) -> Invoice:
    """Parse one PDF. Uses the LLM when available, always cross-checked by regex."""
    adapter = adapter or OfflineAdapter()
    text = extract_text(pdf_path, ocr)
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
