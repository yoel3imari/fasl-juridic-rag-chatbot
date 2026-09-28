"""Pure per-file extraction worker for bulk ingest (no DB, no file writes).

Runs in-process (``workers=1``) or inside a ``ProcessPoolExecutor``: input is
a picklable :class:`ExtractJob`, output a picklable :class:`ExtractResult`
whose records cross IPC to the single-writer parent. One bad file yields a
``quarantined`` result here; it never raises for content reasons.

Page policy (plan todo 12): PyMuPDF text fast path; Tesseract ``ara+fra`` ONLY
for pages whose stripped text is < ``min_chars``, rendered at ``dpi`` with a
bounded ``ocr_timeout``. ``ocr_mode="off"`` disables the fallback. A missing
engine / missing language data / timeout marks the page empty (counted at
quarantine time), never crashes the file.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Any

from app.infrastructure.authority.extractor import Chunk
from app.library.artifacts import build_record
from app.services.library_seed import chunk_id as make_chunk_id

_OCR_LANGS = "ara+fra"


@dataclass(frozen=True)
class ExtractJob:
    """Picklable worker input: plain fields only, no ORM, no DB handles."""

    rel: str
    abs_path: str
    source: str
    version: str
    edition: str
    category: str
    file_sha: str
    min_chars: int
    dpi: int
    ocr_timeout: int
    ocr_mode: str


@dataclass
class ExtractResult:
    """Picklable worker output: records cross IPC; the parent writes."""

    rel: str
    status: str  # "extracted" | "quarantined"
    records: list[dict[str, Any]]
    quarantine_reason: str | None = None
    ocr_pages: int = 0
    ocr_attempted: int = 0


def needs_ocr(page_text: str, min_chars: int) -> bool:
    """OCR-policy decision: True when the text layer is effectively empty."""
    return len((page_text or "").strip()) < min_chars


def _ocr_png(png_bytes: bytes, page_no: int, timeout: int) -> str:
    """OCR one rendered page with Tesseract ``ara+fra`` + bounded timeout."""
    import shutil

    from app.domain.ingestion.errors import OcrUnavailableError

    if shutil.which("tesseract") is None:
        raise OcrUnavailableError(page_no=page_no)
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise OcrUnavailableError(page_no=page_no) from exc
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            return pytesseract.image_to_string(img, lang=_OCR_LANGS, timeout=timeout)
    except Exception as exc:
        raise OcrUnavailableError(
            page_no=page_no, reason=f"tesseract failed: {exc}"
        ) from exc


def _extract_pages(
    pdf_path: str, job: ExtractJob
) -> tuple[list[tuple[int, str]], int, int]:
    """PyMuPDF fast path + per-page OCR fallback. Raises on corrupt PDFs.

    Returns ``(pages, ocr_ok, ocr_attempted)``: attempted counts every page
    that needed OCR (engine present or not); ok counts decoded pages.
    """
    import fitz

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ValueError(f"corrupt: not a readable PDF ({exc})") from exc
    pages: list[tuple[int, str]] = []
    ocr_ok = 0
    ocr_attempted = 0
    try:
        if len(doc) == 0:
            raise ValueError("corrupt: PDF has no pages")
        for i, page in enumerate(doc):
            text = page.get_text()
            if not needs_ocr(text, job.min_chars) or job.ocr_mode == "off":
                pages.append((i + 1, text))
                continue
            ocr_attempted += 1
            try:
                png = bytes(page.get_pixmap(dpi=job.dpi).tobytes("png"))
                text = _ocr_png(png, i + 1, job.ocr_timeout)
                ocr_ok += 1
            except Exception:
                text = ""  # OCR-failed page: empty, counted at quarantine time
            pages.append((i + 1, text))
    finally:
        doc.close()
    return pages, ocr_ok, ocr_attempted


def split_pages_fallback(
    pages: list[tuple[int, str]], source_path: str | None
) -> list[Chunk]:
    """Article-blind page fallback for content the T11 segmenter can't cap.

    Used ONLY when :func:`split_statute_structure` raises its Granite-cap
    backstop (article-blind per-page/preamble chunks over 512 tokens). Each
    page becomes one ``section-pN`` chunk, or ``split_article`` pieces with a
    ``continued_from`` chain when the page itself exceeds the cap. Same
    guarantees as T11: real page numbers, ``{"code": …}`` hierarchy, served
    length <= cap, continuations keep their section label.
    """
    from app.domain.authority.article_split import split_article
    from app.domain.authority.granite_tokens import MAX_GRANITE_TOKENS, count_granite_tokens
    from app.domain.authority.law_patterns import code_for

    code = code_for(source_path)
    chunks: list[Chunk] = []
    for page_no, text in pages:
        body = (text or "").strip()
        if not body:
            continue
        label = f"section-p{page_no}"
        pieces = split_article(body)
        if len(pieces) == 1:
            chunks.append(
                Chunk(
                    text=pieces[0],
                    article_or_section=label,
                    hierarchy={"code": code},
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
                        hierarchy={"code": code},
                        page=page_no,
                        continued_from=None if k == 0 else prev_label,
                    )
                )
                prev_label = piece_label
    for c in chunks:
        n = count_granite_tokens(c.text)
        if n > MAX_GRANITE_TOKENS:
            raise RuntimeError(
                f"fallback chunk {c.article_or_section!r} exceeds cap: {n}"
            )
        c.token_count = n
    return chunks


def run_job(job: ExtractJob) -> ExtractResult:
    """Extract + chunk + record-build ONE file. Pure: no DB, no JSON writes."""
    from app.infrastructure.authority.extractor import split_statute_structure

    if not os.path.exists(job.abs_path):
        return ExtractResult(job.rel, "quarantined", [], "missing: source file gone")
    try:
        pages, ocr_pages, ocr_attempted = _extract_pages(job.abs_path, job)
    except ValueError as exc:  # corrupt PDF
        return ExtractResult(job.rel, "quarantined", [], str(exc))
    except Exception as exc:  # never crash the worker on one bad file
        return ExtractResult(job.rel, "quarantined", [], f"corrupt: {exc}")
    try:
        chunks = split_statute_structure(pages, source_path=job.abs_path)
    except RuntimeError as exc:
        # T11 cap backstop on article-blind chunks: degrade to the page-split
        # fallback (same cap guarantee) instead of quarantining real content.
        if "exceeds Granite cap" not in str(exc):
            return ExtractResult(job.rel, "quarantined", [], f"chunking-failed: {exc}")
        try:
            chunks = split_pages_fallback(pages, source_path=job.abs_path)
        except Exception as fallback_exc:
            return ExtractResult(
                job.rel, "quarantined", [], f"chunking-failed: {fallback_exc}"
            )
    except Exception as exc:
        return ExtractResult(job.rel, "quarantined", [], f"chunking-failed: {exc}")
    if not chunks:
        usable = sum(1 for _, t in pages if t and t.strip())
        if usable == 0 and ocr_attempted > 0:
            reason = (
                f"ocr-failed: 0 usable pages after {ocr_attempted} OCR pages "
                f"({ocr_pages} decoded)"
            )
        elif usable == 0:
            reason = "empty: no extractable text on any page"
        else:
            reason = "empty: chunker emitted zero chunks"
        return ExtractResult(
            job.rel, "quarantined", [], reason, ocr_pages, ocr_attempted
        )
    records = [
        build_record(
            chunk_id=make_chunk_id(
                job.source,
                job.version,
                job.edition,
                c.article_or_section,
                c.text,
                page=c.page,
                ordinal=n,
            ),
            text=c.text,
            page=c.page or 0,
            hierarchy=dict(c.hierarchy or {}),
            article=c.article_or_section,
            tokens=c.token_count or 0,
            source=job.source,
            version=job.version,
            edition=job.edition,
            category=job.category,
            file_sha=job.file_sha,
        )
        for n, c in enumerate(chunks)
    ]
    return ExtractResult(job.rel, "extracted", records, None, ocr_pages, ocr_attempted)
