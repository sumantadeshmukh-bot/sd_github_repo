"""Answer composer: extractive (offline) or LLM-grounded (online), always cited.

Refusal rules (no hallucination):
  1. best retrieval score < ``min_score``                -> NOT FOUND
  2. the evidence chunk covers < ``min_coverage`` of the
     query's content terms                               -> NOT FOUND
  3. online: the LLM answer must cite only chunk ids it was given, else we fall
     back to the extractive answer; an LLM "NOT_FOUND" is respected.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .llm import LLMAdapter, OfflineAdapter
from .retriever import Hit, Retriever, analyze

log = logging.getLogger(__name__)
NOT_FOUND = "Not found in the documents available to your role."


@dataclass
class Citation:
    doc_id: str
    doc_title: str
    section: str
    chunk_id: str
    score: float


@dataclass
class Answer:
    question: str
    text: str
    found: bool
    citations: list[Citation] = field(default_factory=list)
    mode: str = "offline-extractive"
    confidence: float = 0.0

    def render(self) -> str:
        lines = [f"Q: {self.question}", f"A: {self.text}"]
        for i, c in enumerate(self.citations, 1):
            lines.append(f"   [{i}] {c.doc_title} ({c.doc_id}) > {c.section} > {c.chunk_id} (score {c.score:.2f})")
        lines.append(f"   mode={self.mode} confidence={self.confidence:.2f}")
        return "\n".join(lines)


def _coverage(query: str, text: str) -> float:
    q = {t for t in analyze(query) if " " not in t}
    return len(q & set(analyze(text))) / len(q) if q else 0.0


def _cite(h: Hit) -> Citation:
    c = h.chunk
    return Citation(c.doc_id, c.doc_title, c.section, c.chunk_id, h.score)


class Composer:
    def __init__(self, retriever: Retriever, min_score: float, min_coverage: float, top_k: int = 3,
                 adapter: LLMAdapter | None = None) -> None:
        self.r, self.min_score, self.min_cov, self.k = retriever, min_score, min_coverage, top_k
        self.adapter = adapter or OfflineAdapter()

    def ask(self, question: str, role: str, department: str | None = None) -> Answer:
        hits = self.r.search(question, role, department, self.k)
        if not hits or hits[0].score < self.min_score:
            return Answer(question, NOT_FOUND, False, confidence=hits[0].score if hits else 0.0)
        best = hits[0]
        cov = _coverage(question, best.chunk.index_text)
        if cov < self.min_cov:
            return Answer(question, NOT_FOUND, False, confidence=best.score)
        support = [h for h in hits if h.score >= max(self.min_score, 0.8 * best.score)]
        if self.adapter.available:
            online = self._llm(question, support)
            if online:
                return online
        return self._extractive(question, support)

    def _extractive(self, question: str, support: list[Hit]) -> Answer:
        """Pick the 2 sentences from the best chunk most similar to the question."""
        best = support[0]
        sents = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", best.chunk.text) if s]
        sims = (self.r.vec.transform(sents) @ self.r.vec.transform([question]).T).toarray().ravel()
        top = sorted(sorted(range(len(sents)), key=lambda i: -sims[i])[:2])  # keep document order
        text = " ".join(sents[i] for i in top) + " [1]"
        # cite exactly the chunk the sentences were copied from
        return Answer(question, text, True, [_cite(best)], confidence=best.score)

    def _llm(self, question: str, support: list[Hit]) -> Answer | None:
        ctx = "\n\n".join(f"[{h.chunk.chunk_id}] ({h.chunk.doc_title} > {h.chunk.section})\n{h.chunk.text}"
                          for h in support)
        system = ("Answer ONLY from the context. Cite every claim with its [chunk_id]. "
                  "If the context does not answer the question, reply exactly NOT_FOUND.")
        out = self.adapter.complete(system, f"Context:\n{ctx}\n\nQuestion: {question}")
        if not out:
            return None
        if out.strip() == "NOT_FOUND":
            return Answer(question, NOT_FOUND, False, mode=f"llm-{self.adapter.name}", confidence=support[0].score)
        cited = set(re.findall(r"\[([A-Z]+-[A-Z]+-\d+#[a-z0-9-]+)\]", out))
        allowed = {h.chunk.chunk_id for h in support}
        if not cited or not cited <= allowed:
            log.warning("LLM answer had missing/unknown citations %s; using extractive answer", cited - allowed)
            return None
        return Answer(question, out.strip(), True, [_cite(h) for h in support if h.chunk.chunk_id in cited],
                      mode=f"llm-{self.adapter.name}", confidence=support[0].score)
