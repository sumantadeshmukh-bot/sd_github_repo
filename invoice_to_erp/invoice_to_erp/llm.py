"""Pluggable LLM adapters with a deterministic offline fallback.

Design rules (shared by all three demos):
- The pipeline never *requires* an LLM. ``get_adapter()`` returns ``OfflineAdapter``
  unless an API key is present in the environment, so demos run end to end offline.
- Online adapters are thin, dependency-free (stdlib ``urllib``) and only make a
  network call when their key is set AND the pipeline asks them to.
- Every adapter returns ``None`` on any failure; callers then use their own
  deterministic fallback. An LLM can improve a result, never break the pipeline.

Environment variables:
    LLM_PROVIDER       optional: "offline" | "gemini" | "claude" (forces a choice)
    GEMINI_API_KEY     enables GeminiAdapter   (model: GEMINI_MODEL)
    ANTHROPIC_API_KEY  enables ClaudeAdapter   (model: ANTHROPIC_MODEL)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)

# Model names change often - override via env rather than editing code.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"
TIMEOUT_S = 30


class LLMAdapter(ABC):
    """Minimal interface the pipelines depend on."""

    name: str = "base"

    @property
    @abstractmethod
    def available(self) -> bool:
        """True when this adapter can actually be called."""

    @abstractmethod
    def complete(self, system: str, prompt: str) -> str | None:
        """Return raw text, or None if unavailable / failed."""

    def complete_json(self, system: str, prompt: str) -> dict[str, Any] | None:
        """Ask for JSON and parse it defensively. None on any failure."""
        text = self.complete(system + "\nRespond with a single JSON object only.", prompt)
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            log.warning("%s returned non-JSON output; using offline fallback", self.name)
            return None


class OfflineAdapter(LLMAdapter):
    """Never calls anything. Signals callers to use deterministic logic."""

    name = "offline"

    @property
    def available(self) -> bool:
        return False

    def complete(self, system: str, prompt: str) -> str | None:
        return None


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any] | None:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (fixed vendor URL)
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        log.warning("LLM call failed (%s); falling back to offline logic", exc)
        return None


class GeminiAdapter(LLMAdapter):
    """Google Gemini via REST. Untested in the build environment (no key)."""

    name = "gemini"

    def __init__(self) -> None:
        self.key = os.getenv("GEMINI_API_KEY", "")
        self.model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

    @property
    def available(self) -> bool:
        return bool(self.key)

    def complete(self, system: str, prompt: str) -> str | None:
        if not self.available:
            return None
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0},
        }
        data = _post_json(url, {"x-goog-api-key": self.key}, body)
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"] if data else None
        except (KeyError, IndexError, TypeError):
            return None


class ClaudeAdapter(LLMAdapter):
    """Anthropic Claude via the Messages API. Untested in the build environment (no key)."""

    name = "claude"

    def __init__(self) -> None:
        self.key = os.getenv("ANTHROPIC_API_KEY", "")
        self.model = os.getenv("ANTHROPIC_MODEL", DEFAULT_CLAUDE_MODEL)

    @property
    def available(self) -> bool:
        return bool(self.key)

    def complete(self, system: str, prompt: str) -> str | None:
        if not self.available:
            return None
        body = {
            "model": self.model,
            "max_tokens": 2000,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        data = _post_json("https://api.anthropic.com/v1/messages", headers, body)
        try:
            return "".join(b.get("text", "") for b in data["content"]) if data else None
        except (KeyError, TypeError):
            return None


def get_adapter() -> LLMAdapter:
    """Pick an adapter: LLM_PROVIDER if set, else first available key, else offline."""
    forced = os.getenv("LLM_PROVIDER", "").lower().strip()
    candidates: dict[str, LLMAdapter] = {"claude": ClaudeAdapter(), "gemini": GeminiAdapter()}
    if forced == "offline":
        return OfflineAdapter()
    if forced in candidates:
        chosen = candidates[forced]
        if not chosen.available:
            log.warning("LLM_PROVIDER=%s but its API key is not set; using offline mode", forced)
            return OfflineAdapter()
        return chosen
    for adapter in candidates.values():
        if adapter.available:
            return adapter
    return OfflineAdapter()
