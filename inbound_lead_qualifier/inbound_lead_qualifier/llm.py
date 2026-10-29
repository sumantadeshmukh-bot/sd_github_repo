"""Pluggable LLM adapters with a deterministic offline fallback.

Design rules (shared by all three demos):
- The pipeline never *requires* an LLM. ``get_adapter()`` returns ``OfflineAdapter``
  unless an API key is present in the environment, so demos run end to end offline.
- Online adapters are thin, dependency-free (stdlib ``urllib``) and only make a
  network call when their key is set AND the pipeline asks them to.
- Every adapter returns ``None`` on any failure; callers then use their own
  deterministic fallback. An LLM can improve a result, never break the pipeline.

- Online calls are retried with exponential backoff (timeouts, HTTP 429/5xx), and every
  call appends a ``UsageRecord`` (tokens, latency, attempts, estimated cost). Retry,
  timeout and per-model price settings come from the ``"llm"`` block of config.json via
  ``configure()``. Prices are illustrative: check current pricing before quoting costs.

Environment variables:
    LLM_PROVIDER       optional: "offline" | "gemini" | "claude" (forces a choice)
    GEMINI_API_KEY     enables GeminiAdapter   (model: GEMINI_MODEL)
    ANTHROPIC_API_KEY  enables ClaudeAdapter   (model: ANTHROPIC_MODEL)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Model names change often - override via env rather than editing code.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"
TIMEOUT_S = 30
RETRY_STATUS = {408, 429, 500, 502, 503, 504}

# Overridden by configure(config["llm"]); defaults keep the module usable on its own.
_CFG: dict[str, Any] = {"timeout_s": TIMEOUT_S, "max_retries": 3, "backoff_base_s": 1.0, "pricing": {}}
_urlopen = urllib.request.urlopen  # indirection so tests can stub the network
_sleep = time.sleep


@dataclass
class UsageRecord:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    attempts: int = 1
    cost_usd: float | None = None
    ok: bool = True
    parse_failure: bool = False
    error: str = ""


_USAGE: list[UsageRecord] = []


def configure(llm_cfg: dict[str, Any] | None) -> None:
    """Apply the ``"llm"`` block of a demo's config.json (timeout, retries, backoff, prices)."""
    if llm_cfg:
        _CFG.update({k: v for k, v in llm_cfg.items() if not k.startswith("_")})


def reset_usage() -> None:
    _USAGE.clear()


def usage_records() -> list[UsageRecord]:
    return list(_USAGE)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD estimate from config prices (per million tokens); None if the model has no price entry."""
    prices = _CFG.get("pricing", {})
    price = prices.get(model) or next((v for k, v in prices.items() if model.startswith(k)), None)
    if not price:
        return None
    return (input_tokens * price["input_per_mtok"] + output_tokens * price["output_per_mtok"]) / 1_000_000


def usage_summary() -> dict[str, Any]:
    costs = [r.cost_usd for r in _USAGE if r.cost_usd is not None]
    return {
        "calls": len(_USAGE),
        "failed_calls": sum(not r.ok for r in _USAGE),
        "parse_failures": sum(r.parse_failure for r in _USAGE),
        "input_tokens": sum(r.input_tokens for r in _USAGE),
        "output_tokens": sum(r.output_tokens for r in _USAGE),
        "total_latency_s": round(sum(r.latency_s for r in _USAGE), 3),
        "estimated_cost_usd": round(sum(costs), 6) if costs else None,
        "pricing_note": "Per-model prices come from config.json; check current pricing before quoting costs.",
    }


def write_usage_log(path: Path) -> None:
    """Write the per-call records and their summary as JSON (no prompts or responses are stored)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"summary": usage_summary(), "calls": [asdict(r) for r in _USAGE]}, indent=2),
                    encoding="utf-8")


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
            self._mark_parse_failure()
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            log.warning("%s returned non-JSON output; using offline fallback", self.name)
            self._mark_parse_failure()
            return None

    @staticmethod
    def _mark_parse_failure() -> None:
        if _USAGE:
            _USAGE[-1].parse_failure = True


class OfflineAdapter(LLMAdapter):
    """Never calls anything. Signals callers to use deterministic logic."""

    name = "offline"

    @property
    def available(self) -> bool:
        return False

    def complete(self, system: str, prompt: str) -> str | None:
        return None


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> tuple[dict[str, Any] | None, int, str]:
    """POST JSON with retries. Returns (data or None, attempts used, last error text)."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json", **headers}
    )
    attempts, err = 0, ""
    max_attempts = 1 + int(_CFG["max_retries"])
    while attempts < max_attempts:
        attempts += 1
        try:
            with _urlopen(req, timeout=_CFG["timeout_s"]) as resp:  # noqa: S310 (fixed vendor URL)
                return json.loads(resp.read().decode()), attempts, ""
        except urllib.error.HTTPError as exc:
            err = f"HTTP {exc.code}"
            if exc.code not in RETRY_STATUS:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            err = str(exc)
        if attempts < max_attempts:
            delay = float(_CFG["backoff_base_s"]) * 2 ** (attempts - 1)
            log.warning("LLM call failed (%s); retry %d/%d in %.1fs", err, attempts, max_attempts - 1, delay)
            _sleep(delay)
    log.warning("LLM call failed after %d attempt(s) (%s); falling back to offline logic", attempts, err)
    return None, attempts, err


def _record(provider: str, model: str, t0: float, attempts: int, err: str,
            tokens: tuple[int, int] = (0, 0), ok: bool = True) -> None:
    _USAGE.append(UsageRecord(provider, model, tokens[0], tokens[1], round(time.monotonic() - t0, 3), attempts,
                              estimate_cost(model, *tokens) if ok else None, ok, False, err))


class GeminiAdapter(LLMAdapter):
    """Google Gemini via REST. Live-tested only when a key is set (see RESULTS.md)."""

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
        t0 = time.monotonic()
        data, attempts, err = _post_json(url, {"x-goog-api-key": self.key}, body)
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"] if data else None
            meta = (data or {}).get("usageMetadata", {})
            tokens = (int(meta.get("promptTokenCount", 0)), int(meta.get("candidatesTokenCount", 0)))
        except (KeyError, IndexError, TypeError):
            text, tokens, err = None, (0, 0), err or "unexpected response shape"
        _record(self.name, self.model, t0, attempts, err, tokens, ok=text is not None)
        return text


class ClaudeAdapter(LLMAdapter):
    """Anthropic Claude via the Messages API. Live-tested only when a key is set (see RESULTS.md)."""

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
        t0 = time.monotonic()
        data, attempts, err = _post_json("https://api.anthropic.com/v1/messages", headers, body)
        try:
            text = "".join(b.get("text", "") for b in data["content"]) if data else None
            usage = (data or {}).get("usage", {})
            tokens = (int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)))
        except (KeyError, TypeError):
            text, tokens, err = None, (0, 0), err or "unexpected response shape"
        _record(self.name, self.model, t0, attempts, err, tokens, ok=text is not None)
        return text


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
