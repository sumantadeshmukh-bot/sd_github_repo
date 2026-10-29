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
from .normalize import normalize_amounts

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
    # amounts first: "50 lakh" / "Rs. 50,00,000" both become the single token amt5000000
    toks = [stem(t) for t in TOKEN_RE.findall(normalize_amounts(text.lower())) if t not in ENGLISH_STOP_WORDS and t not in EXTRA_STOP]
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

    def _scores(self, query: str) -> np.ndarray:
        """Similarity of the query to every chunk (TF-IDF cosine). Subclasses swap the scoring only."""
        q = self.vec.transform([query])
        return (self.matrix @ q.T).toarray().ravel()

    def search(self, query: str, role: str, department: str | None = None, k: int = 3) -> list[Hit]:
        mask = self.allowed(role)
        if department:
            mask &= np.array([c.department.lower() == department.lower() for c in self.chunks])
        withheld = int((~self.allowed(role)).sum())
        log.debug("role=%s: %d chunks withheld by access control", role, withheld)
        scores = self._scores(query)
        scores[~mask] = -1.0  # filtered chunks are never returned
        order = np.argsort(-scores)[:k]
        return [Hit(self.chunks[i], float(scores[i])) for i in order if scores[i] > 0]


class EmbeddingRetriever(Retriever):
    """Optional dense retriever, enabled with ``--retriever embedding`` (TF-IDF stays the default).

    Access control, filtering and ranking are inherited unchanged; only the scoring differs.
    Backend ``auto`` uses ``sentence-transformers`` when it is installed and the model is available
    locally, otherwise a local LSA embedding (TruncatedSVD over the TF-IDF matrix: no download, no
    extra dependency). Scores are cosine similarities, so ``min_score`` needs its own value
    (``embedding.min_score`` in config.json).
    """

    def __init__(self, chunks: list[Chunk], role_access: dict[str, list[str]], backend: str = "auto",
                 model: str = "all-MiniLM-L6-v2", lsa_dims: int = 64) -> None:
        super().__init__(chunks, role_access)  # keeps the TF-IDF index for sentence picking
        self.backend = "lsa"
        self._st = None
        if backend in ("auto", "sentence-transformers"):
            try:
                from sentence_transformers import SentenceTransformer

                self._st = SentenceTransformer(model, local_files_only=True)
                self.backend = "sentence-transformers"
            except Exception as exc:  # ImportError, or the model is not cached locally
                if backend == "sentence-transformers":
                    raise RuntimeError(f"sentence-transformers backend unavailable: {exc}") from exc
                log.info("sentence-transformers unavailable (%s); using local LSA embeddings", type(exc).__name__)
        if self._st is not None:
            self.emb = self._st.encode([c.index_text for c in chunks], normalize_embeddings=True)
        else:
            from sklearn.decomposition import TruncatedSVD
            from sklearn.preprocessing import normalize

            dims = max(2, min(lsa_dims, len(chunks) - 1, self.matrix.shape[1] - 1))
            self._svd = TruncatedSVD(n_components=dims, random_state=0)
            self.emb = normalize(self._svd.fit_transform(self.matrix))
        log.info("EmbeddingRetriever backend: %s", self.backend)

    def _scores(self, query: str) -> np.ndarray:
        if self._st is not None:
            qv = self._st.encode([query], normalize_embeddings=True)[0]
        else:
            from sklearn.preprocessing import normalize

            qv = normalize(self._svd.transform(self.vec.transform([query])))[0]
        return np.asarray(self.emb @ qv).ravel()
