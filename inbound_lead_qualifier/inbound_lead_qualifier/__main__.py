"""CLI.

python -m inbound_lead_qualifier demo            start server, post the 10 sample leads, stop, summarise
python -m inbound_lead_qualifier serve           run the webhook on 127.0.0.1:8765 until Ctrl+C
python -m inbound_lead_qualifier send [--file samples/leads.json] [--url ...]   post leads to a running server
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import threading
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from .pipeline import LeadPipeline
from .server import make_server

DEFAULT_URL = "http://127.0.0.1:8765/webhook/enquiry"


def post_leads(file: Path, url: str) -> list[dict]:
    """Simulate the website form calling the webhook. Only localhost URLs are intended."""
    out = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route via a proxy
    for lead in json.loads(file.read_text(encoding="utf-8")):
        req = urllib.request.Request(url, data=json.dumps(lead).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Webhook-Token": os.getenv("WEBHOOK_TOKEN", "")})
        try:
            with opener.open(req, timeout=10) as r:  # noqa: S310
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
        out.append(body)
        print(f"{body.get('lead_id', '?'):14s} {body.get('status', '?'):18s} {str(body.get('tier')):8s} "
              f"score={body.get('score')}  {'; '.join(body.get('reasons', []))[:90]}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Inbound lead qualifier demo")
    ap.add_argument("cmd", choices=["demo", "serve", "send"])
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--file", default="samples/leads.json")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text())
    logging.basicConfig(level=cfg.get("log_level", "INFO"), format="%(levelname)s %(name)s: %(message)s")

    if a.cmd == "send":
        post_leads(Path(a.file), a.url)
        return
    if a.cmd == "demo":
        shutil.rmtree(cfg["output_dir"], ignore_errors=True)  # fresh, reproducible run
    pipe = LeadPipeline(cfg)
    srv = make_server(pipe, port=a.port)
    if a.cmd == "serve":
        print(f"Listening on http://127.0.0.1:{a.port}/webhook/enquiry (Ctrl+C to stop)")
        srv.serve_forever()
        return
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        res = post_leads(Path(a.file), f"http://127.0.0.1:{a.port}/webhook/enquiry")
    finally:
        srv.shutdown()
    status = Counter(r.get("status") for r in res)
    tiers = Counter(r.get("tier") for r in res if r.get("status") == "QUALIFIED")
    summary = {"leads": len(res), "status": dict(status), "qualified_by_tier": dict(tiers)}
    Path(cfg["output_dir"], "summary.json").write_text(json.dumps(summary, indent=2))
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
