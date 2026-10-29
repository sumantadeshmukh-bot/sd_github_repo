"""Wiring helpers shared by the CLI, the evaluator and tests."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .answer import Composer
from .corpus import load_corpus
from .llm import get_adapter
from .retriever import Retriever

log = logging.getLogger(__name__)


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build(cfg: dict, base: Path = Path(".")) -> Composer:
    chunks = load_corpus(base / cfg["doc_dir"])
    log.info("Indexed %d chunks from %s", len(chunks), cfg["doc_dir"])
    retriever = Retriever(chunks, cfg["role_access"])
    adapter = get_adapter()
    log.info("LLM mode: %s", adapter.name if adapter.available else "offline (extractive answers)")
    return Composer(retriever, cfg["min_score"], cfg["min_coverage"], cfg["top_k"], adapter)
