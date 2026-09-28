"""PDF/text structure extractor preserving statute and decision hierarchy.

Splits statute text into article-level chunks with PER-CHUNK ``page``,
``hierarchy`` (code/law/book/part/chapter) and ``article_or_section``.
Article boundaries (``المادة``/``Article`` and numbered ``الفصل`` items)
are primary; a chunk is split mid-article only when the Granite token cap
requires it, with an N-token overlap and ``continued_from`` linkage where
every continuation keeps its article reference.

Token counts are measured with the ACTUAL Granite tokenizer
(``ibm-granite/granite-embedding-107m-multilingual``, ``<s>``/``</s>``
special tokens included) -- never a chars/4 proxy. The cap is 512 tokens
(Granite ``max_position_embeddings=514``), so served post-tokenization
length is always <= 512 and the server never truncates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.authority.article_split import split_article
from app.domain.authority.granite_tokens import MAX_GRANITE_TOKENS, count_granite_tokens
from app.domain.authority.law_patterns import (
    FOLDER_CODE_MAP,
    HEADER_CONT_RE,
    HEADING_RES,
    LAW_HEADER_RE,
    code_for,
    item_label,
    law_type_for,
)

__all__ = [
    "MAX_GRANITE_TOKENS",
    "FOLDER_CODE_MAP",
    "Chunk",
    "count_granite_tokens",
    "split_statute_structure",
    "extract_chunks",
    "extract_text",
]


@dataclass
class Chunk:
    text: str
    article_or_section: str | None = None
    hierarchy: dict[str, str | None] = field(default_factory=dict)
    page: int | None = None
    # Granite token count incl. special tokens (== served post-tokenization length).
    token_count: int | None = None
    # Set on split continuations: article_or_section of the previous piece.
    continued_from: str | None = None


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


def split_statute_structure(
    pages: list[tuple[int, str]], source_path: str | None = None
) -> list[Chunk]:
    """Split pages into article-level chunks with per-chunk page/hierarchy.

    Headings (book/part/chapter) and the law header are tracked by POSITION:
    each chunk carries the headings in effect where IT starts. Over-long
    articles split with a GRANITE_OVERLAP_TOKENS-token overlap and
    ``continued_from`` linkage; every continuation keeps the article label.
    """
    pages = [(n, t) for n, t in pages if t and t.strip()]
    if not pages:
        return []

    # Line walk with page + offset tracking.
    lines: list[tuple[int, str]] = []  # (page_no, line)
    for n, t in pages:
        for line in t.splitlines():
            lines.append((n, line))
    if not any(line.strip() for _, line in lines):
        return []

    code = code_for(source_path)
    law: str | None = None
    law_type: str | None = None
    current: dict[str, str | None] = {"book": None, "part": None, "chapter": None}

    # First pass: locate the doc law header (first header-form line) plus any
    # wrapped continuation lines immediately following it.
    header_extra: set[int] = set()
    for i, (_, line) in enumerate(lines):
        if not line.strip():
            continue
        if law is None:
            m = LAW_HEADER_RE.match(line)
            if m:
                law_type = law_type_for(m.group(1))
                law = line.strip()[:300]
                continue
            if item_label(line):
                break
        else:
            if item_label(line) or any(rx.match(line) for rx, _ in HEADING_RES):
                break
            if HEADER_CONT_RE.match(line):
                header_extra.add(i)
                law = f"{law} {line.strip()}".strip()[:300]
                continue
            break

    def hierarchy_snapshot() -> dict[str, str | None]:
        h: dict[str, str | None] = {"code": code}
        if law_type:
            h["law_type"] = law_type
        if law:
            h["law"] = law
        for k in ("book", "part", "chapter"):
            if current[k]:
                h[k] = current[k]
        return h

    # Second pass: cut at item boundaries, tracking headings by position.
    # Each item: (start_line_idx, page_no, label, hierarchy).
    items: list[tuple[int, int, str, dict]] = []
    preamble_end: int = 0
    preamble_hier: dict = {}
    for idx, (page_no, line) in enumerate(lines):
        if not line.strip():
            continue
        label = item_label(line)
        if label:
            if not items:
                preamble_end = idx
                preamble_hier = hierarchy_snapshot()
            items.append((idx, page_no, label, hierarchy_snapshot()))
            continue
        for rx, level in HEADING_RES:
            m = rx.match(line)
            if m:
                # A numbered الفصل is an item (handled above), never a chapter.
                rest = m.group(1)
                if level == "chapter" and re.match(r"\s*[0-9٠-٩]", rest):
                    break
                current[level] = line.strip()[:300]
                if level == "book":
                    current["part"] = None
                    current["chapter"] = None
                elif level == "part":
                    current["chapter"] = None
                break

    chunks: list[Chunk] = []
    if not items:
        # Non-statute (judgment/commentary/unnumbered): per-page sections
        # with REAL page numbers.
        for n, t in pages:
            chunks.append(
                Chunk(
                    text=t.strip(),
                    article_or_section=f"section-p{n}",
                    hierarchy={
                        "code": code,
                        **({"law_type": law_type} if law_type else {}),
                    },
                    page=n,
                )
            )
    else:
        # Preamble before the first item is kept, never dropped -- but pure
        # structural lines (law header, headings) are metadata, not content,
        # and never form a preamble chunk alone.
        def _is_structural(j: int, line: str) -> bool:
            if j in header_extra:
                return True
            if LAW_HEADER_RE.match(line) or item_label(line):
                return True
            return any(rx.match(line) for rx, _ in HEADING_RES)

        preamble = "\n".join(
            line
            for j, (_, line) in enumerate(lines[:preamble_end])
            if line.strip() and not _is_structural(j, line)
        ).strip()
        if preamble:
            chunks.append(
                Chunk(
                    text=preamble,
                    article_or_section="preamble",
                    hierarchy=dict(preamble_hier),
                    page=items[0][1],
                )
            )
        for i, (start_idx, page_no, label, hier) in enumerate(items):
            end_idx = items[i + 1][0] if i + 1 < len(items) else len(lines)
            body = "\n".join(line for _, line in lines[start_idx:end_idx]).strip()
            if not body:
                continue
            pieces = split_article(body)
            if len(pieces) == 1:
                chunks.append(
                    Chunk(
                        text=pieces[0],
                        article_or_section=label,
                        hierarchy=dict(hier),
                        page=page_no,
                    )
                )
            else:
                total = len(pieces)
                prev_label = label
                for k, piece in enumerate(pieces):
                    piece_label = f"{label} · part {k + 1}/{total}"
                    chunks.append(
                        Chunk(
                            text=piece,
                            article_or_section=piece_label,
                            hierarchy=dict(hier),
                            page=page_no,
                            continued_from=None if k == 0 else prev_label,
                        )
                    )
                    prev_label = piece_label

    # Hard guarantee: served post-tokenization length <= cap, no truncation.
    for c in chunks:
        n = count_granite_tokens(c.text)
        if n > MAX_GRANITE_TOKENS:
            raise RuntimeError(
                f"chunk {c.article_or_section!r} exceeds Granite cap: {n} > {MAX_GRANITE_TOKENS}"
            )
        c.token_count = n
    return chunks


def extract_chunks(file_path: str) -> list[Chunk]:
    return split_statute_structure(extract_text(file_path), source_path=file_path)
