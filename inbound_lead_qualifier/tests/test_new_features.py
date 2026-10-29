"""Tests for: rate limiting, request size limits, structured JSON logs, CRM encryption at rest."""
import http.client
import json
import logging
import threading
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from inbound_lead_qualifier.crmstore import CrmStore, EncryptionConfigError, generate_key
from inbound_lead_qualifier.jsonlog import JsonFormatter, setup_logging
from inbound_lead_qualifier.pipeline import CRM_COLUMNS, LeadPipeline
from inbound_lead_qualifier.ratelimit import RateLimiter
from inbound_lead_qualifier.server import make_server

ROOT = Path(__file__).resolve().parents[1]
LEADS = json.loads((ROOT / "samples" / "leads.json").read_text())


def make_pipe(tmp_path, **cfg_over) -> LeadPipeline:
    cfg = json.loads((ROOT / "config.json").read_text()) | cfg_over
    cfg["output_dir"] = str(tmp_path / "out")
    return LeadPipeline(cfg)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Server:
    """Run the webhook on an ephemeral port; talk to it with http.client for full header control."""

    def __init__(self, pipe, clock=None):
        self.srv = make_server(pipe, port=0, **({"clock": clock} if clock else {}))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def post(self, body: bytes | None, headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Content-Type": "application/json", **(headers or {})}
        conn.request("POST", "/webhook/enquiry", body=body, headers=h)
        r = conn.getresponse()
        data = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, data, dict(r.getheaders())

    def close(self):
        self.srv.shutdown()


@pytest.fixture()
def server(tmp_path):
    s = Server(make_pipe(tmp_path))
    yield s
    s.close()


# ---------------------------------------------------------------- rate limiting
def test_sliding_window_blocks_then_recovers():
    clock = Clock()
    rl = RateLimiter(3, 60, clock=clock)
    assert [rl.check("a")[0] for _ in range(3)] == [True, True, True]
    ok, retry = rl.check("a")
    assert ok is False and 0 < retry <= 60
    assert rl.check("b")[0] is True            # other clients are unaffected
    clock.t += 61
    assert rl.check("a")[0] is True            # window passed


def test_rate_limiter_memory_is_bounded():
    clock = Clock()
    rl = RateLimiter(1, 60, max_keys=5, clock=clock)
    for i in range(50):
        rl.check(f"ip{i}")
    assert len(rl._hits) <= 5


def test_webhook_returns_429_with_retry_after(tmp_path):
    clock = Clock()
    s = Server(make_pipe(tmp_path, limits={"rate_per_minute": 2}), clock)
    try:
        codes = [s.post(json.dumps(LEADS[0]).encode())[0] for _ in range(3)]
        assert codes == [202, 202, 429]
        status, body, headers = s.post(json.dumps(LEADS[0]).encode())
        assert status == 429 and int(headers["Retry-After"]) >= 1 and "rate limit" in body["error"]
        clock.t += 61
        assert s.post(json.dumps(LEADS[0]).encode())[0] == 202
    finally:
        s.close()


def test_x_forwarded_for_ignored_by_default(tmp_path):
    s = Server(make_pipe(tmp_path, limits={"rate_per_minute": 1}), Clock())
    try:
        body = json.dumps(LEADS[0]).encode()
        assert s.post(body, {"X-Forwarded-For": "1.1.1.1"})[0] == 202
        assert s.post(body, {"X-Forwarded-For": "2.2.2.2"})[0] == 429  # spoofing a new IP does not help
    finally:
        s.close()


def test_x_forwarded_for_used_only_when_trusted(tmp_path):
    s = Server(make_pipe(tmp_path, limits={"rate_per_minute": 1, "trust_forwarded_for": True}), Clock())
    try:
        body = json.dumps(LEADS[0]).encode()
        assert s.post(body, {"X-Forwarded-For": "1.1.1.1"})[0] == 202
        assert s.post(body, {"X-Forwarded-For": "2.2.2.2"})[0] == 202
        assert s.post(body, {"X-Forwarded-For": "1.1.1.1"})[0] == 429
    finally:
        s.close()


# ---------------------------------------------------------------- request size / shape limits
def test_oversized_body_rejected_with_413(tmp_path):
    s = Server(make_pipe(tmp_path, limits={"max_body_bytes": 1024}))
    try:
        status, body, _ = s.post(json.dumps({**LEADS[0], "message": "x" * 5000}).encode())
        assert status == 413 and "1024" in body["error"]
        assert s.post(json.dumps(LEADS[0]).encode())[0] == 202  # normal-size request still fine
    finally:
        s.close()


def test_missing_and_invalid_content_length(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.putrequest("POST", "/webhook/enquiry")
    conn.putheader("Content-Type", "application/json")
    conn.endheaders()  # no Content-Length
    assert conn.getresponse().status == 411
    conn.close()
    for bad in ("abc", "-5"):
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.putrequest("POST", "/webhook/enquiry")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", bad)
        conn.endheaders()
        assert conn.getresponse().status == 400, bad
        conn.close()


def test_wrong_content_type_and_bad_json(server):
    assert server.post(b"{}", {"Content-Type": "text/plain"})[0] == 415
    assert server.post(b"{not json")[0] == 400
    assert server.post(b"[1, 2]")[0] == 400
    assert server.post(b"\xff\xfe")[0] == 400                     # not UTF-8
    assert server.post(b"[" * 5000 + b"]" * 5000)[0] == 400       # over-nested JSON must not crash the handler
    assert server.post(json.dumps(LEADS[0]).encode())[0] == 202   # server still healthy


def test_token_still_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBHOOK_TOKEN", "s3cret")
    s = Server(make_pipe(tmp_path))
    try:
        body = json.dumps(LEADS[0]).encode()
        assert s.post(body)[0] == 401
        assert s.post(body, {"X-Webhook-Token": "s3cret"})[0] == 202
    finally:
        s.close()


# ---------------------------------------------------------------- structured logs
def test_json_formatter_emits_one_object_with_fields():
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "request", (), None)
    rec.fields = {"status": 202, "lead_id": "L-1"}
    out = json.loads(JsonFormatter().format(rec))
    assert out["msg"] == "request" and out["level"] == "INFO" and out["status"] == 202 and "ts" in out


def test_request_log_line_has_no_personal_data(tmp_path, caplog):
    s = Server(make_pipe(tmp_path))
    try:
        with caplog.at_level(logging.INFO, logger="inbound_lead_qualifier.server"):
            s.post(json.dumps(LEADS[0]).encode())
    finally:
        s.close()
    recs = [r for r in caplog.records if r.name.endswith("server")]
    assert recs, "no request log record"
    line = JsonFormatter().format(recs[-1])
    f = json.loads(line)
    assert f["event"] == "http_request" and f["status"] == 202 and f["lead_id"].startswith("L-")
    assert {"request_id", "duration_ms", "client", "method", "path"} <= set(f)
    assert LEADS[0]["email"] not in line and LEADS[0]["name"] not in line


def test_setup_logging_json_and_text(capsys):
    setup_logging({"log_format": "json", "log_level": "INFO"})
    logging.getLogger("t").info("hello", extra={"fields": {"k": 1}})
    assert json.loads(capsys.readouterr().err.strip().splitlines()[-1])["k"] == 1
    setup_logging({"log_format": "text"})
    logging.getLogger("t").info("plain")
    assert "plain" in capsys.readouterr().err


# ---------------------------------------------------------------- CRM encryption at rest
def test_crm_encrypted_on_disk_and_readable_through_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    pipe = make_pipe(tmp_path)
    pipe.process(LEADS[0])
    out = tmp_path / "out"
    raw = (out / "crm_leads.csv.enc").read_bytes()
    assert not (out / "crm_leads.csv").exists()
    for secret in (LEADS[0]["email"], LEADS[0]["name"], b"lead_id".decode(), LEADS[0]["phone"]):
        assert secret.encode() not in raw
    rows = pipe.crm.rows()
    assert len(rows) == 1 and rows[0]["email"] == LEADS[0]["email"].lower() and rows[0]["name"] == LEADS[0]["name"]


def test_encrypted_upsert_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    pipe = make_pipe(tmp_path)
    pipe.process(LEADS[0])
    pipe.process(LEADS[0])
    pipe.process(LEADS[1])
    assert len(pipe.crm.rows()) == 2


def test_dev_mode_plaintext_with_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        pipe = make_pipe(tmp_path)
    assert "DEV MODE" in caplog.text and not pipe.crm.encrypted
    pipe.process(LEADS[0])
    assert (tmp_path / "out" / "crm_leads.csv").exists()
    assert LEADS[0]["name"] in (tmp_path / "out" / "crm_leads.csv").read_text()


def test_required_encryption_refuses_to_start_without_key(tmp_path):
    with pytest.raises(EncryptionConfigError, match="required"):
        make_pipe(tmp_path, encryption={"required": True})


def test_invalid_key_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(EncryptionConfigError, match="valid Fernet key"):
        make_pipe(tmp_path)


def test_wrong_key_cannot_decrypt(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    make_pipe(tmp_path).process(LEADS[0])
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    with pytest.raises(EncryptionConfigError, match="cannot decrypt"):
        CrmStore(tmp_path / "out", CRM_COLUMNS).rows()


def test_encrypted_file_without_key_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    make_pipe(tmp_path).process(LEADS[0])
    monkeypatch.delenv("CRM_ENCRYPTION_KEY")
    with pytest.raises(EncryptionConfigError, match="no key"):
        CrmStore(tmp_path / "out", CRM_COLUMNS).rows()  # never silently starts a second, plaintext CRM


def test_key_rotation_new_key_first(tmp_path, monkeypatch):
    old, new = generate_key(), generate_key()
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", old)
    make_pipe(tmp_path).process(LEADS[0])
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", f"{new},{old}")
    pipe = make_pipe(tmp_path)
    assert len(pipe.crm.rows()) == 1          # old data still readable
    pipe.process(LEADS[1])                    # rewrite now uses the new key
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", new)
    assert len(CrmStore(tmp_path / "out", CRM_COLUMNS).rows()) == 2


def test_plaintext_crm_migrated_when_key_added(tmp_path, monkeypatch):
    pipe = make_pipe(tmp_path)
    pipe.process(LEADS[0])
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    pipe2 = make_pipe(tmp_path)
    pipe2.process(LEADS[1])
    assert len(pipe2.crm.rows()) == 2 and (tmp_path / "out" / "crm_leads.csv.enc").exists()
    assert (tmp_path / "out" / "crm_leads.csv").exists()  # the old copy is NOT deleted automatically


def test_export_plaintext_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    pipe = make_pipe(tmp_path)
    pipe.process(LEADS[0])
    n = pipe.crm.export_plaintext(tmp_path / "export.csv")
    assert n == 1 and LEADS[0]["name"] in (tmp_path / "export.csv").read_text()


def test_generated_key_is_valid_fernet():
    Fernet(generate_key().encode())


def test_parked_leads_still_hold_no_pii_when_encrypted(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_ENCRYPTION_KEY", generate_key())
    pipe = make_pipe(tmp_path)
    pipe.process(LEADS[3])  # no consent
    text = pipe.parked_path.read_text()
    assert LEADS[3]["email"] not in text and LEADS[3]["name"] not in text
