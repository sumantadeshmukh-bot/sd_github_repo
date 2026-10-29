"""TF-IDF retrieval with role-based access control applied BEFORE scoring.

Chunks the caller's role may not see are removed from the candidate set, so they
can never be ranked, quoted or leaked through an LLM prompt.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

from .corpus import Chunk

log = logging.getLogger(__name__)
TOKEN_RE = re.compile(r"[a-z0-9]+")
EXTRA_STOP = {"company", "policy", "tarangini", "retail", "does", "rs"}


def stem(tok: str) -> str:
    """Tiny suffix stripper (keeps the demo dependency-free)."""
    for suf in ("ing", "ed", "es", "al", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            tok = tok[: -len(suf)]
            break
    # fold a trailing "e" so require/required and purchase/purchases agree
    return tok[:-1] if tok.endswith("e") and len(tok) > 4 else tok


def analyze(text: str) -> list[str]:
    toks = [stem(t) for t in TOKEN_RE.findall(text.lower()) if t not in ENGLISH_STOP_WORDS and t not in EXTRA_STOP]
    return toks + [f"{a} {b}" for a, b in zip(toks, toks[1:])]


@dataclass
class Hit:
    chunk: Chunk
    score: float


class Retriever:
    def __init__(self, chunks: list[Chunk], role_access: dict[str, list[str]]) -> None:
        self.chunks = chunks
        self.role_access = role_access
        self.vec = TfidfVectorizer(analyzer=analyze, sublinear_tf=True)
        self.matrix = self.vec.fit_transform([c.index_text for c in chunks])

    def allowed(self, role: str) -> np.ndarray:
        if role not in self.role_access:
            raise ValueError(f"unknown role '{role}'")
        levels = set(self.role_access[role])
        return np.array([c.access in levels for c in self.chunks])

    def search(self, query: str, role: str, department: str | None = None, k: int = 3) -> list[Hit]:
        mask = self.allowed(role)
        if department:
            mask &= np.array([c.department.lower() == department.lower() for c in self.chunks])
        withheld = int((~self.allowed(role)).sum())
        log.debug("role=%s: %d chunks withheld by access control", role, withheld)
        q = self.vec.transform([query])
        scores = (self.matrix @ q.T).toarray().ravel()
        scores[~mask] = -1.0  # filtered chunks are never returned
        order = np.argsort(-scores)[:k]
        return [Hit(self.chunks[i], float(scores[i])) for i in order if scores[i] > 0]
