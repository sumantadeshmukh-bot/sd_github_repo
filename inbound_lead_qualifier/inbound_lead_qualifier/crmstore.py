"""CRM store: a CSV that is encrypted at rest with Fernet when a key is provided.

Key: environment variable named by ``encryption.key_env`` (default ``CRM_ENCRYPTION_KEY``). Several
comma-separated keys are accepted for rotation (the first encrypts, all decrypt). Generate one with
``python -m inbound_lead_qualifier genkey``. Keep it in a secret manager, never in the repo.

DEV MODE: with no key the CRM is written as plaintext ``crm_leads.csv`` and a warning is logged.
Set ``"encryption": {"required": true}`` to refuse to start without a key (use this in production).
Fernet gives authenticated encryption (AES-128-CBC + HMAC-SHA256); the whole file is re-encrypted on
every upsert, which is fine for a demo-sized CRM but not for large tables (use a database with
column or disk encryption there).
"""
from __future__ import annotations

import csv
import io
import logging
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger(__name__)


class EncryptionConfigError(RuntimeError):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


class CrmStore:
    def __init__(self, directory: Path, columns: list[str], enc_cfg: dict[str, Any] | None = None) -> None:
        enc_cfg = enc_cfg or {}
        self.columns = columns
        self._lock = threading.Lock()  # the HTTP server is threaded; serialise read-modify-write
        self.plain_path = directory / "crm_leads.csv"
        self.enc_path = directory / "crm_leads.csv.enc"
        raw = os.getenv(enc_cfg.get("key_env", "CRM_ENCRYPTION_KEY"), "").strip()
        self._fernet: MultiFernet | None = None
        if raw:
            try:
                self._fernet = MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])
            except (ValueError, TypeError) as exc:
                raise EncryptionConfigError("CRM encryption key is not a valid Fernet key "
                                            "(generate one with: python -m inbound_lead_qualifier genkey)") from exc
        elif enc_cfg.get("required", False):
            raise EncryptionConfigError("CRM encryption is required but no key is set in the environment")
        else:
            log.warning("CRM_ENCRYPTION_KEY is not set: DEV MODE, the CRM is stored as plaintext CSV")

    @property
    def encrypted(self) -> bool:
        return self._fernet is not None

    @property
    def path(self) -> Path:
        return self.enc_path if self.encrypted else self.plain_path

    def exists(self) -> bool:
        return self.path.exists()

    def _read_text(self) -> str:
        if self._fernet:
            if not self.enc_path.exists():
                if self.plain_path.exists():  # first run after turning encryption on: migrate a copy
                    log.warning("Migrating plaintext CRM into the encrypted store; delete %s yourself once verified",
                                self.plain_path)
                    return self.plain_path.read_text(encoding="utf-8")
                return ""
            try:
                return self._fernet.decrypt(self.enc_path.read_bytes()).decode("utf-8")
            except InvalidToken as exc:
                raise EncryptionConfigError("cannot decrypt the CRM: wrong or rotated-out key") from exc
        if self.enc_path.exists() and not self.plain_path.exists():
            raise EncryptionConfigError(f"{self.enc_path.name} is encrypted but no key is set in the environment")
        return self.plain_path.read_text(encoding="utf-8") if self.plain_path.exists() else ""

    def rows(self) -> list[dict[str, str]]:
        text = self._read_text()
        return list(csv.DictReader(io.StringIO(text))) if text else []

    def upsert(self, row: dict[str, Any]) -> None:
        """Idempotent by lead_id: re-posting a lead replaces its row."""
        with self._lock:
            existing = {r["lead_id"]: r for r in self.rows()}
            existing[row["lead_id"]] = row
            buf = io.StringIO(newline="")
            wr = csv.DictWriter(buf, fieldnames=self.columns)
            wr.writeheader()
            wr.writerows(existing.values())
            data = buf.getvalue().encode("utf-8")
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_bytes(self._fernet.encrypt(data) if self._fernet else data)
            tmp.replace(self.path)  # atomic swap: a crash never leaves a half-written CRM
            if self._fernet:
                self.enc_path.chmod(0o600)

    def export_plaintext(self, out: Path) -> int:
        """Decrypt to a plaintext CSV for an authorised export. Returns the row count."""
        rows = self.rows()
        with out.open("w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=self.columns)
            wr.writeheader()
            wr.writerows(rows)
        return len(rows)
