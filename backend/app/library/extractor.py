"""PDF/text structure extractor preserving statute and decision hierarchy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ARTICLE_RE = re.compile(r"^\s*(?:المادة|Article)\s+(\d+)", re.IGNORECASE | re.MULTILINE)
HEADING_RES = [
    (
        re.compile(r"^\s*(?:الكتاب| Livre)\s+(.+)$", re.IGNORECASE | re.MULTILINE),
        "book",
    ),
    (
        re.compile(
            r"^\s*(?:الباب| Partie|Titre)\s+(.+)$", re.IGNORECASE | re.MULTILINE
        ),
        "part",
    ),
    (
        re.compile(r"^\s*(?:الفصل| Chapitre)\s+(.+)$", re.IGNORECASE | re.MULTILINE),
        "chapter",
    ),
]


@dataclass
class Chunk:
    text: str
    article_or_section: str | None = None
    hierarchy: dict[str, str | None] = field(default_factory=dict)
    page: int | None = None


def extract_text(file_path: str) -> list[tuple[int, str]]:
    """Return list of (page_no, text). Uses PyMuPDF when available, else plain-text read."""
    path = Path(file_path)
    if not path.exists():
        return []
    if path.suffix.lower() == ".pdf":
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(str(path))
            pages = [(i + 1, page.get_text()) for i, page in enumerate(doc)]
            doc.close()
            return [(n, t) for n, t in pages if t.strip()]
        except ImportError:
            return []
    return [(1, path.read_text(encoding="utf-8", errors="ignore"))]


def split_statute_structure(pages: list[tuple[int, str]]) -> list[Chunk]:
    """Split statute text into article-level chunks keeping Code->Book->Part->Chapter->Article."""
    full = "\n".join(t for _, t in pages)
    if not full.strip():
        return []
    matches = list(ARTICLE_RE.finditer(full))
    if not matches:
        # Non-statute (judgment/commentary): keep decision/section structure per page.
        return [
            Chunk(
                text=t.strip(),
                article_or_section=f"section-p{n}",
                hierarchy={"level": "section"},
            )
            for n, t in pages
            if t.strip()
        ]
    chunks: list[Chunk] = []
    current: dict[str, str | None] = {"book": None, "part": None, "chapter": None}
    for line in full.splitlines():
        for rx, level in HEADING_RES:
            m = rx.match(line)
            if m:
                current[level] = m.group(0).strip()
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full)
        body = full[m.start() : end].strip()
        chunks.append(
            Chunk(
                text=body,
                article_or_section=f"Article {m.group(1)}",
                hierarchy={
                    "code": "Code du Travail",
                    **{k: v for k, v in current.items() if v},
                },
            )
        )
    return chunks


def extract_chunks(file_path: str) -> list[Chunk]:
    return split_statute_structure(extract_text(file_path))
