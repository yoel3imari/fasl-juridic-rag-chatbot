"""Native section identification with page/span provenance.

Headings (Article N / المادة N / Objet / contract titles) start new sections;
everything else flows into the current section. Faithful text keeps original
spacing/case; normalized text collapses whitespace and lowercases for search.
Sections longer than MAX_SECTION_CHARS split into parent/child segments —
children partition the parent, never truncate it.
"""

from __future__ import annotations

import re
from typing import Final

from app.ingestion.schemas import PageText, SectionResult

MAX_SECTION_CHARS: Final[int] = 2000
CHILD_CHARS: Final[int] = 1500

_HEADING: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:"
    r"(?:Article|ARTICLE|المادة)\s+\S+.*"
    r"|(?:Objet|OBJET)\s*:.*"
    r"|(?:CONTRAT DE TRAVAIL|Section|Chapitre|الفصل|الباب).{0,80}"
    r")\s*$"
)

_SENTENCE_SPLIT: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?\n])\s+")


def _clean_title(raw: str) -> str:
    """Collapse whitespace in titles; faithful body text keeps original spacing."""
    return re.sub(r"\s+", " ", raw).strip()[:200]


def normalize(text: str) -> str:
    """Collapse whitespace and lowercase for search; faithful copy stays raw."""
    return " ".join(text.split()).lower()


def build_sections(pages: list[PageText]) -> list[SectionResult]:
    """Split pages into titled sections, then parent/child-split long ones."""
    full, page_of_offset = _join_pages(pages)
    if not full.strip():
        return []
    raw = _split_raw(full, page_of_offset, pages)
    out: list[SectionResult] = []
    for index, (
        title,
        body,
        page_start,
        page_end,
        start,
        end,
        conf,
        review,
    ) in enumerate(raw):
        faithful = body if body.strip() else title
        base = SectionResult(
            section_id=f"s{index + 1}",
            title=title,
            page_start=page_start,
            page_end=page_end,
            span_start=start,
            span_end=end,
            faithful_text=faithful,
            normalized_text=normalize(faithful),
            ocr_confidence=conf,
            needs_review=review,
        )
        out.extend(_parent_child_split(base))
    return out


def _join_pages(pages: list[PageText]) -> tuple[str, list[tuple[int, int]]]:
    """Concatenate page texts; record (page_no, offset) boundaries."""
    chunks: list[str] = []
    bounds: list[tuple[int, int]] = []
    offset = 0
    for page in pages:
        bounds.append((page.page_no, offset))
        chunks.append(page.text)
        offset += len(page.text) + 1
    return ("\n".join(chunks), bounds)


def _split_raw(
    full: str, bounds: list[tuple[int, int]], pages: list[PageText]
) -> list[tuple[str, str, int, int, int, int, float | None, bool]]:
    """Cut the concatenated text at heading lines, tracking pages and spans."""
    lines = full.splitlines(keepends=True)
    cuts: list[tuple[int, str]] = []
    offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped and _HEADING.match(line) and offset > 0:
            cuts.append((offset, stripped))
        offset += len(line)
    starts = [0, *[c[0] for c in cuts]]
    titles = [c[1] for c in cuts]
    first_title = _first_line_title(full)
    sections: list[tuple[str, str, int, int, int, int, float | None, bool]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(full)
        body = full[start:end].strip()
        if not body:
            continue
        if i == 0:
            title = _clean_title(first_title)
        else:
            title = _clean_title(titles[i - 1])
        page_start = _page_at(bounds, start)
        page_end = _page_at(bounds, max(start, end - 1))
        conf, review = _page_signals(pages, page_start, page_end)
        sections.append((title, body, page_start, page_end, start, end, conf, review))
    return sections


def _first_line_title(full: str) -> str:
    for line in full.splitlines():
        if line.strip():
            return line.strip()[:120]
    return "Section 1"


def _page_at(bounds: list[tuple[int, int]], offset: int) -> int:
    current = bounds[0][0] if bounds else 1
    for page_no, start in bounds:
        if offset >= start:
            current = page_no
    return current


def _page_signals(
    pages: list[PageText], start: int, end: int
) -> tuple[float | None, bool]:
    covered = [p for p in pages if start <= p.page_no <= end]
    if not covered:
        return None, False
    confs = [p.ocr_confidence for p in covered if p.ocr_confidence is not None]
    conf = min(confs) if confs else None
    return conf, any(p.needs_review for p in covered)


def _parent_child_split(base: SectionResult) -> list[SectionResult]:
    """Split long sections into children partitioning the parent text."""
    if len(base.faithful_text) <= MAX_SECTION_CHARS:
        return [base]
    pieces = _chunk_sentences(base.faithful_text)
    children: list[SectionResult] = []
    cursor = base.span_start
    for i, piece in enumerate(pieces):
        piece_start = base.faithful_text.find(piece, max(0, cursor - base.span_start))
        start = base.span_start + (piece_start if piece_start >= 0 else 0)
        end = start + len(piece)
        cursor = end
        children.append(
            SectionResult(
                section_id=f"{base.section_id}.c{i + 1}",
                title=f"{base.title} (part {i + 1}/{len(pieces)})",
                page_start=base.page_start,
                page_end=base.page_end,
                span_start=start,
                span_end=end,
                faithful_text=piece,
                normalized_text=normalize(piece),
                parent_section_id=base.section_id,
                ocr_confidence=base.ocr_confidence,
                needs_review=base.needs_review,
            )
        )
    return [base, *children]


def _chunk_sentences(text: str) -> list[str]:
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for part in parts:
        current.append(part)
        size += len(part)
        if size >= CHILD_CHARS:
            chunks.append(" ".join(current).strip())
            current, size = [], 0
    if current:
        chunks.append(" ".join(current).strip())
    if len(chunks) == 1 and len(chunks[0]) > MAX_SECTION_CHARS:
        hard = chunks[0]
        return [hard[i : i + CHILD_CHARS] for i in range(0, len(hard), CHILD_CHARS)]
    return chunks
