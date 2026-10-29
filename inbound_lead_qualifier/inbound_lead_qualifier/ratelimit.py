"""In-memory sliding-window rate limiter, keyed by client address.

Single-process only: with several workers or servers, use a shared store (Redis) or the
reverse proxy / API gateway's limiter instead. Memory is bounded by ``max_keys``.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable


class RateLimiter:
    def __init__(self, max_requests: int, window_s: float = 60.0, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max, self.window, self.max_keys, self.clock = max_requests, window_s, max_keys, clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, float]:
        """Record a request. Returns (allowed, retry_after_seconds)."""
        now = self.clock()
        with self._lock:
            if key not in self._hits and len(self._hits) >= self.max_keys:
                self._evict(now)
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.max:
                return False, max(0.0, self.window - (now - q[0]))
            q.append(now)
            return True, 0.0

    def _evict(self, now: float) -> None:
        stale = [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]
        for k in stale or [next(iter(self._hits))]:  # nothing stale: drop the oldest key
            del self._hits[k]
