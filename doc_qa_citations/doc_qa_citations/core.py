"""Wiring helpers shared by the CLI, the evaluator and tests."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .answer import Composer
from .corpus import load_corpus
from .llm import configure, get_adapter
from .retriever import EmbeddingRetriever, Retriever

log = logging.getLogger(__name__)


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(cfg: dict, base: Path = Path("."), retriever: str | None = None) -> Composer:
    """``retriever``: "tfidf" (default, from config) or "embedding" (optional dense retriever)."""
    configure(cfg.get("llm"))
    kind = retriever or cfg.get("retriever", "tfidf")
    chunks = load_corpus(base / cfg["doc_dir"])
    log.info("Indexed %d chunks from %s", len(chunks), cfg["doc_dir"])
    min_score = cfg["min_score"]
    if kind == "embedding":
        emb = cfg.get("embedding", {})
        retr: Retriever = EmbeddingRetriever(chunks, cfg["role_access"], emb.get("backend", "auto"),
                                             emb.get("model", "all-MiniLM-L6-v2"), emb.get("lsa_dims", 64))
        min_score = emb.get("min_score", min_score)
    elif kind == "tfidf":
        retr = Retriever(chunks, cfg["role_access"])
    else:
        raise ValueError(f"unknown retriever '{kind}' (use tfidf or embedding)")
    adapter = get_adapter()
    log.info("Retriever: %s | LLM mode: %s", kind, adapter.name if adapter.available else "offline (extractive answers)")
    return Composer(retr, min_score, cfg["min_coverage"], cfg["top_k"], adapter)
