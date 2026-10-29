"""Load markdown policy documents and split them into heading-aware chunks.

Each document starts with a simple front-matter block (title, doc_id, department,
access). A section can tighten access with a heading suffix such as
``## Approval limits {access: confidential}``. Sections never inherit a *looser*
level than their document.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ACCESS_ORDER = ["public", "internal", "confidential"]
HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)(?:\s*\{access:\s*(?P<access>\w+)\})?\s*$")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    doc_title: str
    section: str
    department: str
    access: str
    text: str

    @property
    def index_text(self) -> str:
        # Repeat title words so section headings weigh more than body text.
        return f"{self.doc_title}. {self.section}. {self.section}. {self.text}"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _front_matter(raw: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.DOTALL)
    if not m:
        raise ValueError("document is missing front matter")
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    return {k.strip(): v.strip() for k, v in meta.items()}, raw[m.end():]


def _stricter(a: str, b: str) -> str:
    return max(a, b, key=ACCESS_ORDER.index)


def chunk_document(path: Path, max_words: int = 90) -> list[Chunk]:
    meta, body = _front_matter(path.read_text(encoding="utf-8"))
    chunks: list[Chunk] = []
    section, access, buf = None, meta["access"], []

    def flush() -> None:
        if not section or not buf:
            return
        words, part, n = " ".join(buf).split(), [], 0
        # split long sections on sentence boundaries into <= max_words pieces
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", " ".join(words))
        for s in sentences:
            if part and len(" ".join(part + [s]).split()) > max_words:
                chunks.append(_mk(" ".join(part), n))
                part, n = [], n + 1
            part.append(s)
        if part:
            chunks.append(_mk(" ".join(part), n))

    def _mk(text: str, n: int) -> Chunk:
        return Chunk(f"{meta['doc_id']}#{_slug(section)}-{n}", meta["doc_id"], meta["title"], section,
                     meta["department"], access, text)

    for line in body.splitlines():
        h = HEADING_RE.match(line)
        if h:
            flush()
            section, buf = h["title"].strip(), []
            access = _stricter(meta["access"], h["access"] or meta["access"])
        elif line.strip() and not line.startswith("# "):
            buf.append(line.strip())
    flush()
    return chunks


def load_corpus(doc_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for p in sorted(doc_dir.glob("*.md")):
        chunks.extend(chunk_document(p))
    return chunks
