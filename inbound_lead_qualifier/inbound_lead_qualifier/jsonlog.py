"""Structured (JSON-lines) logging for the webhook and pipeline.

One JSON object per line: ts, level, logger, msg plus any fields passed as
``log.info("request", extra={"fields": {...}})``. Never pass raw email/phone/name in fields:
log ids, statuses and masked values only.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rec = {
            "ts": datetime.fromtimestamp(record.created, IST).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            rec["exc"] = self.formatException(record.exc_info)
        return json.dumps(rec, default=str)


def setup_logging(cfg: dict) -> None:
    """``log_format`` in config.json: "json" (default) or "text"."""
    handler = logging.StreamHandler(sys.stderr)
    if cfg.get("log_format", "json") == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(cfg.get("log_level", "INFO"))
