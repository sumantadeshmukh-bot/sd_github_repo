"""online_check.py must work end to end with a stand-in adapter (no network, no key)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import online_check  # noqa: E402


class FakeAdapter:
    """Looks online but answers nothing useful: every demo must fall back to its offline logic."""
    name, model, available = "fake", "fake-1", True

    def complete(self, system, prompt):
        return None

    def complete_json(self, system, prompt):
        return None


def test_run_checks_with_fallbacks_only():
    res = online_check.run_checks(FakeAdapter())
    inv = res["invoice_to_erp"]
    assert inv["invoices"] == 6 and inv["parsed_by_llm"] == 0 and inv["status_matches_offline"] == 6
    qa = res["doc_qa_citations"]
    assert qa["heldout"]["cases"] == 15 and qa["dev"]["answers_by_llm"] == 0
    assert res["inbound_lead_qualifier"]["status_and_tier_match_offline"] == 10


def test_render_and_write_replace_section(tmp_path):
    section = online_check.render(online_check.run_checks(FakeAdapter()))
    assert "provider `fake`" in section and "check current pricing" in section
    f = tmp_path / "RESULTS.md"
    f.write_text("# Results\n\nbody\n")
    online_check.write_results(section, f)
    online_check.write_results(section.replace("fake-1", "fake-2"), f)  # second write replaces, not appends
    text = f.read_text()
    assert text.count(online_check.START) == 1 and "fake-2" in text and "fake-1" not in text and "body" in text


def test_skips_cleanly_without_key(monkeypatch, capsys):
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sys, "argv", ["online_check.py"])
    assert online_check.main() == 0 and "SKIPPED" in capsys.readouterr().out


def test_each_demo_gets_its_own_adapter_and_usage_log():
    """A real run must create one adapter per demo module so usage lands in that demo's own log."""
    seen = []

    def factory(llm_module):
        seen.append(llm_module.__name__)
        return FakeAdapter()

    online_check.run_checks(factory)
    assert seen == ["invoice_to_erp.llm", "invoice_to_erp.llm", "doc_qa_citations.llm", "inbound_lead_qualifier.llm"]
