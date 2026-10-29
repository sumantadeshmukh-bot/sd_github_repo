"""Minimal webhook receiver (stdlib only). Binds to localhost by default.

POST /webhook/enquiry   JSON body -> 202 {lead_id, status, tier, score} | 400 | 401 | 413
GET  /health            -> 200 {"ok": true}

If WEBHOOK_TOKEN is set in the environment, callers must send it in the
X-Webhook-Token header (constant-time comparison).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .pipeline import LeadPipeline

log = logging.getLogger(__name__)
MAX_BODY = 64 * 1024


def make_server(pipeline: LeadPipeline, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    token = os.getenv("WEBHOOK_TOKEN", "")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args) -> None:  # route access log through logging
            log.debug("%s " + fmt, self.address_string(), *args)

        def do_GET(self) -> None:
            self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/webhook/enquiry":
                return self._send(404, {"error": "not found"})
            if token and not hmac.compare_digest(self.headers.get("X-Webhook-Token", ""), token):
                return self._send(401, {"error": "unauthorised"})
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self._send(413, {"error": "payload too large"})
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON object expected")
            except ValueError as exc:
                return self._send(400, {"error": f"bad JSON: {exc}"})
            res = pipeline.process(payload)
            code = 400 if res.status == "REJECTED_INVALID" else 202
            self._send(code, {"lead_id": res.lead_id, "status": res.status, "tier": res.tier,
                              "score": res.score, "reasons": res.reasons})

    return ThreadingHTTPServer((host, port), Handler)
