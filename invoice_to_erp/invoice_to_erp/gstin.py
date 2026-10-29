"""GSTIN format and check-digit validation (GSTN mod-36 algorithm).

GSTIN layout (15 chars):
    2  state code | 10 PAN (AAAAA9999A) | 1 entity no. (1-9, A-Z) | 'Z' | 1 check char
The check character is computed over the first 14 characters with alternating
factors 1, 2 over the base-36 alphabet 0-9A-Z (Luhn mod N style).
"""
from __future__ import annotations

import re

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

# Subset of GST state codes used by the demo (extend as needed).
STATE_CODES: dict[str, str] = {
    "07": "Delhi",
    "09": "Uttar Pradesh",
    "19": "West Bengal",
    "24": "Gujarat",
    "27": "Maharashtra",
    "29": "Karnataka",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "36": "Telangana",
}
STATE_BY_NAME = {v.lower(): k for k, v in STATE_CODES.items()}


def check_char(first14: str) -> str:
    """Compute the GSTIN check character for the first 14 characters."""
    total = 0
    for i, ch in enumerate(first14.upper()):
        product = CHARSET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return CHARSET[(36 - total % 36) % 36]


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    """Build a correctly check-summed (synthetic) GSTIN."""
    body = f"{state}{pan}{entity}Z"
    return body + check_char(body)


def validate_gstin(gstin: str | None) -> list[str]:
    """Return a list of problems; empty list means valid."""
    if not gstin:
        return ["GSTIN missing"]
    g = gstin.strip().upper()
    if not GSTIN_RE.match(g):
        return [f"GSTIN '{g}' does not match the 15-character GSTIN format"]
    problems: list[str] = []
    if g[:2] not in STATE_CODES:
        problems.append(f"GSTIN '{g}' has unknown state code {g[:2]}")
    expected = check_char(g[:14])
    if g[14] != expected:
        problems.append(f"GSTIN '{g}' fails checksum (expected check char '{expected}', found '{g[14]}')")
    return problems
