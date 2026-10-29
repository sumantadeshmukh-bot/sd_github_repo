import csv
import json
import threading
import urllib.request
from pathlib import Path

import pytest

from inbound_lead_qualifier.pipeline import LeadPipeline, domain_of, mask_email
from inbound_lead_qualifier.server import make_server

ROOT = Path(__file__).resolve().parents[1]
LEADS = json.loads((ROOT / "samples" / "leads.json").read_text())


@pytest.fixture()
def pipe(tmp_path) -> LeadPipeline:
    cfg = json.loads((ROOT / "config.json").read_text())
    cfg["output_dir"] = str(tmp_path / "out")
    return LeadPipeline(cfg)


def test_no_consent_is_parked_without_pii(pipe):
    res = pipe.process(LEADS[3])
    assert res.status == "PARKED_NO_CONSENT"
    parked = pipe.parked_path.read_text()
    assert LEADS[3]["email"] not in parked and LEADS[3]["name"] not in parked
    assert not pipe.crm_path.exists()


def test_follow_up_window_blocks_draft(pipe):
    res = pipe.process(LEADS[4])  # enquiry 14 days before reference time
    assert res.status == "WINDOW_EXPIRED" and res.draft_path is None


def test_window_is_configurable(pipe):
    pipe.cfg["follow_up_window_days"] = 30
    assert pipe.process(LEADS[4]).status == "QUALIFIED"


def test_invalid_payload_rejected(pipe):
    res = pipe.process(LEADS[5])
    assert res.status == "REJECTED_INVALID" and any("email" in r for r in res.reasons)


def test_scorecard_is_transparent_and_routes(pipe):
    res = pipe.process(LEADS[0])
    assert res.tier == "Hot" and set(res.breakdown) >= {"budget", "timeline", "role", "company_size", "use_case_fit"}
    assert pipe.process(LEADS[2]).tier == "Nurture"


def test_draft_not_sent_and_audit_masks_pii(pipe):
    res = pipe.process(LEADS[0])
    assert Path(res.draft_path).read_text().startswith("DRAFT - NOT SENT")
    audit = pipe.audit_path.read_text()
    assert LEADS[0]["email"] not in audit and mask_email(LEADS[0]["email"]) in audit


def test_reposting_is_idempotent(pipe):
    pipe.process(LEADS[0])
    pipe.process(LEADS[0])
    with pipe.crm_path.open() as fh:
        assert len(list(csv.DictReader(fh))) == 1


def test_unknown_fields_dropped(pipe):
    lead = {**LEADS[0], "aadhaar_number": "should-not-be-stored"}
    pipe.process(lead)
    assert "should-not-be-stored" not in pipe.crm_path.read_text()


def test_domain_normalisation():
    assert domain_of("https://www.acme.example/contact") == "acme.example"


def test_webhook_end_to_end(pipe):
    srv = make_server(pipe, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/webhook/enquiry"
        req = urllib.request.Request(url, data=json.dumps(LEADS[7]).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5) as r:
            body = json.loads(r.read())
        assert r.status == 202 and body["tier"] == "Hot"
    finally:
        srv.shutdown()


def test_llm_adapter_defaults_to_offline(monkeypatch):
    from inbound_lead_qualifier.llm import get_adapter
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert get_adapter().name == "offline"
    monkeypatch.setenv("LLM_PROVIDER", "claude")  # forced but no key -> still offline, no call made
    assert get_adapter().name == "offline"
