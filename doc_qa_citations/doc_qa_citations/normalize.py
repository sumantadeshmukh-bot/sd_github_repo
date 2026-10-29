"""Numeric / currency normaliser so "50 lakh", "Rs. 50,00,000" and "₹5000000" index as the same token.

Indian documents write amounts with lakh/crore words, rupee symbols and 2-2-3 digit grouping.
Lexical retrieval treats each spelling as different words, so both documents and questions are
rewritten to a canonical ``amt<rupees>`` token (e.g. ``amt5000000``) before tokenising.

Rules (deliberately conservative so ordinary numbers such as "12 days" are left alone):
- a number is treated as money only if it has a currency marker (₹ / Rs / INR) or a scale word
  (lakh, lac, crore, cr, thousand, or an attached k / L as in "5k", "50L");
- other numbers just lose their grouping commas ("10,000" -> "10000").
"""
from __future__ import annotations

import re
from decimal import Decimal

SCALE = {
    "crore": 10**7, "crores": 10**7, "cr": 10**7,
    "lakh": 10**5, "lakhs": 10**5, "lac": 10**5, "lacs": 10**5,
    "thousand": 10**3,
}
ATTACHED_SCALE = {"k": 10**3, "l": 10**5}  # only when glued to the number: "5k", "50L"

AMOUNT_RE = re.compile(
    r"(?<![a-z0-9])(?:(?P<cur>₹|rs\.?|inr)\s*)?"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:(?P<attached>[kl])(?![a-z])|\s*(?P<word>crores?|cr|lakhs?|lacs?|thousand)(?![a-z]))?",
    re.IGNORECASE,
)


def _value(num: str, attached: str | None, word: str | None) -> Decimal:
    value = Decimal(num.replace(",", ""))
    if word:
        value *= SCALE[word.lower()]
    elif attached:
        value *= ATTACHED_SCALE[attached.lower()]
    return value


def normalize_amounts(text: str) -> str:
    """Rewrite money amounts in ``text`` to ``amt<rupees>`` tokens; strip commas from other numbers."""

    def repl(m: re.Match[str]) -> str:
        try:
            value = _value(m["num"], m["attached"], m["word"])
        except ArithmeticError:
            return m.group(0)
        if m["cur"] or m["attached"] or m["word"]:
            return f"amt{int(value.to_integral_value())}"
        return m["num"].replace(",", "")

    return AMOUNT_RE.sub(repl, text)


def parse_amount(text: str) -> int | None:
    """Rupee value of a single amount string, or None. ``parse_amount("50 lakh") == parse_amount("₹50,00,000")``."""
    m = AMOUNT_RE.fullmatch(text.strip())
    if not m:
        return None
    return int(_value(m["num"], m["attached"], m["word"]).to_integral_value())
