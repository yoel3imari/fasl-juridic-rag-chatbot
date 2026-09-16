"""Text extraction per file kind with per-page output and scanned-page OCR.

PDF goes through PyMuPDF (Arabic-capable text layer); pages whose text layer
is effectively empty fall back to the OCR adapter. DOCX uses python-docx.
TXT/MD decode as UTF-8. Corrupt payloads raise CorruptFileError.
"""

from __future__ import annotations

from typing import Final

from app.ingestion import ocr as ocr_mod
from app.ingestion.errors import CorruptFileError, OcrUnavailableError
from app.ingestion.schemas import PageText

TEXT_PAGE_HINT: Final[int] = 20


def extract_pages(content: bytes, suffix: str, filename: str) -> list[PageText]:
    """Extract one PageText per page for PDF, or a single page for DOCX/TXT/MD."""
    normalized = suffix.lower()
    if normalized == ".pdf":
        return _extract_pdf(content, filename)
    if normalized == ".docx":
        return _extract_docx(content, filename)
    return [PageText(page_no=1, text=_decode_text(content, filename))]


def _decode_text(content: bytes, filename: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorruptFileError(
            filename=filename, reason="not valid UTF-8 text"
        ) from exc


def _extract_docx(content: bytes, filename: str) -> list[PageText]:
    import tempfile
    from docx import Document as DocxDocument

    try:
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=True) as tmp:
            tmp.write(content)
            tmp.flush()
            doc = DocxDocument(tmp.name)
            parts = [p.text for p in doc.paragraphs if p.text.strip()]
            for table in doc.tables:
                for row in table.rows:
                    parts.append(" | ".join(cell.text for cell in row.cells))
    except ValueError as exc:
        raise CorruptFileError(
            filename=filename, reason="not a readable DOCX file"
        ) from exc
    text = "\n".join(parts)
    return [PageText(page_no=1, text=text)]


def _extract_pdf(content: bytes, filename: str) -> list[PageText]:
    import pymupdf

    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK -- PyMuPDF raises FzError*/FileDataError; translate to typed error
        raise CorruptFileError(
            filename=filename, reason="not a readable PDF file"
        ) from exc
    pages: list[PageText] = []
    try:
        if len(doc) == 0:
            raise CorruptFileError(filename=filename, reason="PDF has no pages")
        for i, page in enumerate(doc):
            text = page.get_text()
            if len(text.strip()) >= TEXT_PAGE_HINT:
                pages.append(PageText(page_no=i + 1, text=text))
                continue
            image_bytes = bytes(page.get_pixmap(dpi=200).tobytes("png"))
            pages.append(_ocr_scanned_page(image_bytes, i + 1))
    finally:
        doc.close()
    return pages


def _ocr_scanned_page(image_bytes: bytes, page_no: int) -> PageText:
    """Run OCR on one rendered image-only page; missing engine → reviewable."""
    try:
        result = ocr_mod.ocr_page_image_sync(image_bytes, page_no=page_no)
    except OcrUnavailableError:
        return PageText(page_no=page_no, text="", needs_review=True)
    return PageText(
        page_no=page_no,
        text=result.text,
        ocr_confidence=result.confidence,
        ocr_engine=result.engine,
        needs_review=result.confidence < ocr_mod.low_confidence_threshold(),
    )
