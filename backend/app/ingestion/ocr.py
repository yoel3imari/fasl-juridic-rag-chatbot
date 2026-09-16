"""OCR adapter boundary: real engine confidence, never fabricated numbers.

Uses pytesseract DICT output (no pandas dependency). Languages default to
``ara+fra`` with a bounded per-call timeout; a missing engine, missing
language data, or a timeout all surface as OcrUnavailableError so callers
mark the page reviewable instead of failing the upload.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Text plus the engine's own confidence in [0, 1]."""

    text: str
    confidence: float
    engine: str


def low_confidence_threshold() -> float:
    """Minimum mean word confidence accepted without review."""
    from app.config import settings

    return float(settings.OCR_CONFIDENCE_THRESHOLD)


def ocr_languages() -> str:
    """Tesseract language spec, e.g. ara+fra for Moroccan Arabic/French matter."""
    from app.config import settings

    return settings.OCR_LANGUAGES


def ocr_timeout_seconds() -> float:
    """Bounded per-call runtime for the tesseract subprocess."""
    from app.config import settings

    return float(settings.OCR_TIMEOUT_SECONDS)


async def ocr_page_image(image_bytes: bytes, *, page_no: int) -> OcrResult:
    """Async seam used by the pipeline and tests; runs tesseract off the event loop."""
    from anyio import to_thread

    return await to_thread.run_sync(
        lambda: ocr_page_image_sync(image_bytes, page_no=page_no)
    )


def ocr_page_image_sync(image_bytes: bytes, *, page_no: int) -> OcrResult:
    """Run tesseract on PNG bytes; raise OcrUnavailableError when OCR cannot run."""
    from app.ingestion.errors import OcrUnavailableError

    if shutil.which("tesseract") is None:
        raise OcrUnavailableError(page_no=page_no)
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise OcrUnavailableError(page_no=page_no) from exc
    import io

    languages = ocr_languages()
    timeout = ocr_timeout_seconds()
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            data = pytesseract.image_to_data(
                img,
                lang=languages,
                timeout=timeout,
                output_type=pytesseract.Output.DICT,
            )
            text = pytesseract.image_to_string(img, lang=languages, timeout=timeout)
    except pytesseract.TesseractError as exc:
        raise OcrUnavailableError(
            page_no=page_no,
            reason=f"tesseract failed (missing language data?): {exc}",
        ) from exc
    except RuntimeError as exc:
        raise OcrUnavailableError(
            page_no=page_no, reason=f"OCR timed out after {timeout}s: {exc}"
        ) from exc
    confs: list[object] = data.get("conf", []) if isinstance(data, dict) else []
    return OcrResult(
        text=text,
        confidence=_mean_confidence(confs),
        engine=f"tesseract({languages})",
    )


def _mean_confidence(raw_confs: list[object]) -> float:
    """Mean word confidence in [0, 1]; unparseable or empty input scores 0.0."""
    scored: list[int] = []
    for raw in raw_confs:
        if isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            value = int(raw)
        elif isinstance(raw, str):
            try:
                value = int(float(raw))
            except ValueError:
                continue
        else:
            continue
        if value >= 0:
            scored.append(value)
    if not scored:
        return 0.0
    return max(0.0, min(1.0, sum(scored) / len(scored) / 100.0))
