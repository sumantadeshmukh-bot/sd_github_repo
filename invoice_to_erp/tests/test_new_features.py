"""Tests for: multi-rate GST, Tally XML / Zoho CSV exports, LLM retries + usage log, optional OCR."""
import csv
import io
import json
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_to_erp import llm
from invoice_to_erp.exporters import export_tally_xml, export_zoho_csv, load_mapping, slab_taxes
from invoice_to_erp.generate import generate
from invoice_to_erp.gstin import make_gstin
from invoice_to_erp.models import Invoice, LineItem, TaxLine
from invoice_to_erp.parse import extract_text, ocr_available, parse
from invoice_to_erp.pipeline import run
from invoice_to_erp.validate import validate

ROOT = Path(__file__).resolve().parents[1]
MAPPINGS = {"tally": ROOT / "mappings/tally_purchase.json", "zoho": ROOT / "mappings/zoho_bills.json"}


def multi_rate_invoice(**kw) -> Invoice:
    base = dict(
        source_file="m.pdf", invoice_no="M1", invoice_date=date(2026, 10, 1), supplier_name="S (Demo)",
        supplier_gstin=make_gstin("29", "AAKCK1234M"), buyer_gstin=make_gstin("29", "AAECL7788H"),
        place_of_supply="29", taxable_value=Decimal("1000"), cgst=Decimal("85"), sgst=Decimal("85"),
        grand_total=Decimal("1170"), confidence={"invoice_no": 1.0},
        line_items=[LineItem(description="a", hsn_sac="1111", quantity=1, rate=500, amount=500, tax_rate_pct=18),
                    LineItem(description="b", hsn_sac="2222", quantity=1, rate=500, amount=500, tax_rate_pct=5)],
        tax_lines=[TaxLine(kind="CGST", rate_pct=9, amount=45), TaxLine(kind="SGST", rate_pct=9, amount=45),
                   TaxLine(kind="CGST", rate_pct=2.5, amount=12.5), TaxLine(kind="SGST", rate_pct=2.5, amount=12.5)])
    base.update(kw)
    return Invoice(**base)


@pytest.fixture(scope="module")
def run_out(tmp_path_factory):
    d = tmp_path_factory.mktemp("feat")
    generate(d / "in")
    s = run(d / "in", d / "out", 0.75, mappings=MAPPINGS)
    return d, s


# ---------------------------------------------------------------- multi-rate GST
def test_multi_rate_invoice_parses_per_line_rates(tmp_path):
    pdf = next(p for p in generate(tmp_path) if "multirate" in p.name)
    inv = parse(pdf)
    assert [li.tax_rate_pct for li in inv.line_items] == [18, 12, 5]
    assert inv.tax_rate_pct is None and len(inv.tax_lines) == 6
    assert inv.cgst == inv.sgst == Decimal("4620.00")


def test_multi_rate_invoice_passes_validation(run_out):
    d, _ = run_out
    rows = json.loads((d / "out/invoices.json").read_text())
    row = next(r for r in rows if r["invoice"]["source_file"].startswith("inv06"))
    assert row["validation"]["status"] == "OK"
    assert any("Multiple GST rates" in w for w in row["validation"]["warnings"])


def test_wrong_tax_in_one_slab_is_caught():
    inv = multi_rate_invoice()
    assert validate(inv, {}, 0.75).errors == []
    inv.tax_lines[2].amount = Decimal("40")  # 5% slab: CGST 2.5% printed as 40 instead of 12.50
    errs = validate(inv, {}, 0.75).errors
    assert any("GST 5% slab" in e for e in errs)


def test_tax_at_rate_no_line_carries_is_caught():
    inv = multi_rate_invoice(tax_lines=multi_rate_invoice().tax_lines + [TaxLine(kind="IGST", rate_pct=28, amount=10)])
    assert any("no line item carries that rate" in e for e in validate(inv, {}, 0.75).errors)


def test_generic_csv_uses_per_line_rate(run_out):
    d, _ = run_out
    rows = [r for r in csv.DictReader((d / "out/erp_import_bills.csv").open()) if r["Bill Number"] == "HSG/0233"]
    assert sorted(r["Tax Percentage"] for r in rows) == ["12", "18", "5"]
    assert sum(Decimal(r["CGST"]) for r in rows) == Decimal("4620.00")


# ---------------------------------------------------------------- Tally XML
def test_tally_xml_is_well_formed_and_balanced(run_out):
    d, s = run_out
    root = ET.parse(s.outputs["tally"]).getroot()
    vouchers = root.findall(".//VOUCHER")
    assert len(vouchers) == 3  # exceptions are held back
    for v in vouchers:
        amounts = [Decimal(a.text) for a in v.iter("AMOUNT")]
        assert sum(amounts) == 0, v.findtext("VOUCHERNUMBER")  # debits = credits
    multi = next(v for v in vouchers if v.findtext("VOUCHERNUMBER") == "HSG/0233")
    names = [n.text for n in multi.iter("LEDGERNAME")]
    assert "Purchase GST 12%" in names and "Input CGST @ 6%" in names and "Input SGST @ 2.5%" in names


def test_tally_mapping_changes_ledger_names(tmp_path):
    inv = multi_rate_invoice()
    res = validate(inv, {}, 0.75)
    m = load_mapping(MAPPINGS["tally"]) | {"purchase_ledger": "Purch {rate}", "ledger_entry_tag": "ALLLEDGERENTRIES.LIST"}
    export_tally_xml([(inv, res)], m, tmp_path / "t.xml")
    text = (tmp_path / "t.xml").read_text()
    assert "<ALLLEDGERENTRIES.LIST>" in text and "Purch 18" in text and "Purch 5" in text


def test_tally_round_off_entry_balances_voucher(tmp_path):
    inv = multi_rate_invoice(grand_total=Decimal("1170.40"))  # printed total includes Rs.0.40 round-off
    export_tally_xml([(inv, validate(inv, {}, 0.75))], load_mapping(MAPPINGS["tally"]), tmp_path / "t.xml")
    v = ET.parse(tmp_path / "t.xml").getroot().find(".//VOUCHER")
    assert sum(Decimal(a.text) for a in v.iter("AMOUNT")) == 0
    assert "Round Off" in [n.text for n in v.iter("LEDGERNAME")]


def test_slab_taxes_single_rate_invoice():
    inv = multi_rate_invoice(tax_lines=[], tax_rate_pct=Decimal("18"), cgst=Decimal("90"), sgst=Decimal("90"))
    assert slab_taxes(inv) == {Decimal("18"): {"CGST": Decimal("90"), "SGST": Decimal("90")}}


# ---------------------------------------------------------------- Zoho CSV
def test_zoho_csv_headers_follow_mapping(run_out):
    _, s = run_out
    rows = list(csv.reader(open(s.outputs["zoho"], newline="")))
    m = load_mapping(MAPPINGS["zoho"])
    assert rows[0] == [c["column"] for c in m["columns"]]
    assert len(rows) - 1 == 7  # 2 + 2 + 3 lines from the three clean invoices
    assert rows[1][0] == "2026-10-14"


def test_zoho_mapping_is_configurable(tmp_path):
    inv = multi_rate_invoice()
    m = {"date_format": "%d/%m/%Y", "columns": [{"column": "Vendor", "field": "vendor_name"},
                                                {"column": "Date", "field": "bill_date", "format": "date"},
                                                {"column": "Cur", "const": "INR"}]}
    export_zoho_csv([(inv, validate(inv, {}, 0.75))], m, tmp_path / "z.csv")
    lines = (tmp_path / "z.csv").read_text().splitlines()
    assert lines[0] == "Vendor,Date,Cur" and lines[1] == "S (Demo),01/10/2026,INR"


# ---------------------------------------------------------------- LLM retries and usage log
class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def fast_llm(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(llm, "_sleep", sleeps.append)
    llm.reset_usage()
    llm.configure({"max_retries": 2, "backoff_base_s": 1.0, "timeout_s": 5, "pricing": {
        "claude-test": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}}})
    yield sleeps
    llm.reset_usage()
    llm.configure({"max_retries": 3, "pricing": {}})


def _claude(monkeypatch) -> llm.ClaudeAdapter:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test")
    return llm.ClaudeAdapter()


def test_retry_with_backoff_then_success(monkeypatch, fast_llm):
    calls = []

    def fake(req, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", 429, "rate limited", {}, None)
        return _Resp(json.dumps({"content": [{"text": '{"a": 1}'}],
                                 "usage": {"input_tokens": 1000, "output_tokens": 200}}).encode())

    monkeypatch.setattr(llm, "_urlopen", fake)
    assert _claude(monkeypatch).complete_json("sys", "prompt") == {"a": 1}
    assert fast_llm == [1.0] and calls == [5, 5]  # one 1s backoff; configured timeout passed to urlopen
    rec = llm.usage_records()[0]
    assert (rec.attempts, rec.input_tokens, rec.output_tokens, rec.ok) == (2, 1000, 200, True)


def test_non_retryable_status_is_not_retried(monkeypatch, fast_llm):
    calls = []

    def bad_request(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError("u", 400, "bad request", {}, None)

    monkeypatch.setattr(llm, "_urlopen", bad_request)
    assert _claude(monkeypatch).complete("s", "p") is None
    assert len(calls) == 1 and fast_llm == []


def test_retries_exhausted_falls_back_and_logs(monkeypatch, fast_llm):
    def boom(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr(llm, "_urlopen", boom)
    assert _claude(monkeypatch).complete("s", "p") is None
    assert fast_llm == [1.0, 2.0]  # exponential: base * 2**n
    rec = llm.usage_records()[0]
    assert rec.attempts == 3 and rec.ok is False and "slow" in rec.error


def test_usage_log_tokens_cost_and_parse_failure(monkeypatch, fast_llm, tmp_path):
    body = {"content": [{"text": "not json at all"}], "usage": {"input_tokens": 2000, "output_tokens": 500}}
    monkeypatch.setattr(llm, "_urlopen", lambda req, timeout: _Resp(json.dumps(body).encode()))
    assert _claude(monkeypatch).complete_json("s", "p") is None
    s = llm.usage_summary()
    assert (s["input_tokens"], s["output_tokens"], s["parse_failures"]) == (2000, 500, 1)
    assert s["estimated_cost_usd"] == pytest.approx(2000 * 3 / 1e6 + 500 * 15 / 1e6)
    llm.write_usage_log(tmp_path / "u.json")
    assert json.loads((tmp_path / "u.json").read_text())["summary"]["calls"] == 1


def test_unknown_model_has_no_cost():
    assert llm.estimate_cost("some-new-model", 100, 100) is None


@pytest.mark.skipif(not (__import__("os").getenv("ANTHROPIC_API_KEY") or __import__("os").getenv("GEMINI_API_KEY")),
                    reason="no LLM API key set: online test skipped")
def test_online_invoice_parse_smoke(tmp_path):  # pragma: no cover - needs network + key
    pdf = generate(tmp_path)[0]
    inv = parse(pdf, llm.get_adapter())
    assert inv.invoice_no


# ---------------------------------------------------------------- OCR (optional)
def test_ocr_flag_is_harmless_when_text_layer_exists(tmp_path):
    pdf = generate(tmp_path)[0]
    assert extract_text(pdf, ocr=True) == extract_text(pdf, ocr=False)


def test_ocr_missing_dependency_is_skipped_with_warning(tmp_path, monkeypatch, caplog):
    import invoice_to_erp.parse as parse_mod
    from reportlab.pdfgen import canvas
    blank = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(blank))
    c.showPage()
    c.save()
    monkeypatch.setattr(parse_mod, "ocr_available", lambda: False)
    with caplog.at_level("WARNING"):
        assert extract_text(blank, ocr=True).strip() == ""
    assert "OCR requested" in caplog.text


@pytest.mark.skipif(not ocr_available(), reason="pytesseract/tesseract not installed: OCR test skipped")
def test_ocr_reads_image_only_pdf(tmp_path):  # pragma: no cover - only with tesseract
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((20, 80), "Invoice No: OCR-0001", fill="black")
    p = tmp_path / "img.pdf"
    img.save(p)
    assert "OCR" in extract_text(p, ocr=True)
