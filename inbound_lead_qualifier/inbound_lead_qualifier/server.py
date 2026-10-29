"""Minimal webhook receiver (stdlib only). Binds to localhost by default.

POST /webhook/enquiry   JSON body -> 202 {lead_id, status, tier, score}
                        400 bad request | 401 bad token | 411 no length | 413 too large
                        415 not JSON | 429 rate limited (Retry-After header)
GET  /health            -> 200 {"ok": true}

Guards (limits come from config.json "limits"):
- per-client sliding-window rate limit (default 30 requests/minute) -> 429
- request body size limit (default 16 KiB), checked from Content-Length BEFORE reading -> 413
- socket timeout so a slow client cannot hold a thread forever
- every request is logged as one JSON line (id, client, status, duration; no personal data)

If WEBHOOK_TOKEN is set in the environment, callers must send it in the X-Webhook-Token header
(constant-time comparison). The client address is the TCP peer; X-Forwarded-For is ignored unless
``limits.trust_forwarded_for`` is true (only behind a proxy you control, else it is spoofable).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .pipeline import LeadPipeline
from .ratelimit import RateLimiter

log = logging.getLogger(__name__)
DEFAULT_LIMITS = {"rate_per_minute": 30, "max_body_bytes": 16 * 1024, "socket_timeout_s": 10,
                  "trust_forwarded_for": False}


def make_server(pipeline: LeadPipeline, host: str = "127.0.0.1", port: int = 8765,
                clock: Callable[[], float] = time.monotonic) -> ThreadingHTTPServer:
    token = os.getenv("WEBHOOK_TOKEN", "")
    limits = {**DEFAULT_LIMITS, **pipeline.cfg.get("limits", {})}
    limiter = RateLimiter(int(limits["rate_per_minute"]), 60.0, clock=clock)
    max_body = int(limits["max_body_bytes"])

    class Handler(BaseHTTPRequestHandler):
        timeout = limits["socket_timeout_s"]

        def setup(self) -> None:
            super().setup()
            self._t0 = time.monotonic()
            self._rid = uuid.uuid4().hex[:12]
            self._lead_id: str | None = None

        def _client(self) -> str:
            if limits["trust_forwarded_for"]:
                fwd = self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                if fwd:
                    return fwd
            return self.client_address[0]

        def _send(self, code: int, body: dict, headers: dict[str, str] | None = None) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
            log.info("request", extra={"fields": {
                "event": "http_request", "request_id": self._rid, "method": self.command, "path": self.path,
                "status": code, "client": self._client(), "duration_ms": round((time.monotonic() - self._t0) * 1000, 1),
                "lead_id": self._lead_id}})

        def log_message(self, fmt: str, *args) -> None:  # stdlib access log is replaced by the JSON line above
            pass

        def do_GET(self) -> None:
            self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/webhook/enquiry":
                return self._send(404, {"error": "not found"})
            allowed, retry_after = limiter.check(self._client())
            if not allowed:  # checked first: rejected callers cost almost nothing
                return self._send(429, {"error": "rate limit exceeded"}, {"Retry-After": str(int(retry_after) + 1)})
            if token and not hmac.compare_digest(self.headers.get("X-Webhook-Token", ""), token):
                return self._send(401, {"error": "unauthorised"})
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                return self._send(415, {"error": "Content-Type must be application/json"})
            raw_len = self.headers.get("Content-Length")
            if raw_len is None:
                return self._send(411, {"error": "Content-Length required"})
            try:
                length = int(raw_len)
            except ValueError:
                return self._send(400, {"error": "invalid Content-Length"})
            if length < 0:
                return self._send(400, {"error": "invalid Content-Length"})
            if length > max_body:
                self.close_connection = True  # do not read or drain an oversized body
                return self._send(413, {"error": f"payload too large (limit {max_body} bytes)"})
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON object expected")
            except (ValueError, RecursionError) as exc:  # includes UnicodeDecodeError and over-nested JSON
                return self._send(400, {"error": f"bad JSON: {type(exc).__name__}"})
            res = pipeline.process(payload)
            self._lead_id = res.lead_id
            code = 400 if res.status == "REJECTED_INVALID" else 202
            self._send(code, {"lead_id": res.lead_id, "status": res.status, "tier": res.tier,
                              "score": res.score, "reasons": res.reasons})

    return ThreadingHTTPServer((host, port), Handler)
