from decimal import Decimal
from pathlib import Path

import pytest

from invoice_to_erp.generate import generate, inr
from invoice_to_erp.gstin import make_gstin, validate_gstin
from invoice_to_erp.models import Invoice, LineItem
from invoice_to_erp.pipeline import run
from invoice_to_erp.validate import validate


def test_gstin_checksum_roundtrip():
    g = make_gstin("29", "AAECL7788H")
    assert validate_gstin(g) == []
    bad = g[:-1] + ("A" if g[-1] != "A" else "B")
    assert any("checksum" in p for p in validate_gstin(bad))


def test_gstin_format_rejected():
    assert validate_gstin("29ABC") and validate_gstin(None) == ["GSTIN missing"]


def test_indian_number_format():
    assert inr(Decimal("1234567.5")) == "12,34,567.50"


def _inv(**kw) -> Invoice:
    base = dict(source_file="x.pdf", invoice_no="A1", supplier_gstin=make_gstin("29", "AAKCK1234M"),
                buyer_gstin=make_gstin("29", "AAECL7788H"), place_of_supply="29",
                line_items=[LineItem(description="d", hsn_sac="8467", quantity=2, rate=100, amount=200)],
                taxable_value=200, cgst=18, sgst=18, tax_rate_pct=18, grand_total=236,
                confidence={"invoice_no": 1.0})
    base.update(kw)
    from datetime import date
    return Invoice(invoice_date=date(2026, 10, 1), **base)


def test_intra_state_igst_flagged():
    res = validate(_inv(cgst=0, sgst=0, igst=36), {}, 0.75)
    assert any("expected CGST+SGST" in e for e in res.errors)


def test_low_confidence_sets_review_flag():
    res = validate(_inv(confidence={"place_of_supply": 0.6}), {}, 0.75)
    assert res.needs_human_review and res.status == "REVIEW"


@pytest.fixture(scope="module")
def results(tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("run")
    generate(d / "in")
    summary = run(d / "in", d / "out", 0.75)
    report = Path(summary.outputs["exceptions"]).read_text()
    return {"summary": summary, "report": report}


def test_end_to_end_flags_expected_invoices(results):
    s, rpt = results["summary"], results["report"]
    assert (s.total, s.ok, s.exceptions) == (5, 2, 3)
    assert "inv03_coromandel.pdf" in rpt and "7800.00" in rpt  # arithmetic error
    assert "fails checksum" in rpt                               # bad GSTIN
    assert "Duplicate of inv01_kaveri.pdf" in rpt                # duplicate
    assert "place_of_supply" in rpt                              # low-confidence inference


class FakeLLM:
    """Stands in for Gemini/Claude: returns schema JSON with one wrong field."""
    name, available = "fake", True

    def complete_json(self, system, prompt):
        return {"invoice_no": "WRONG-1", "supplier_gstin": make_gstin("29", "AAKCK1234M"),
                "line_items": [], "grand_total": "40108.20"}


def test_llm_path_is_cross_checked(tmp_path):
    from invoice_to_erp.parse import parse
    pdf = generate(tmp_path)[0]
    inv = parse(pdf, FakeLLM())
    assert inv.parser == "llm-fake"
    assert inv.confidence["invoice_no"] == 0.5  # disagreement with regex -> human review
