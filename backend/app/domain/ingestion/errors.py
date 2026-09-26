"""Typed errors for the matter ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MatterNotFoundError(Exception):
    """Raised when the target matter does not exist."""

    matter_id: int

    def __str__(self) -> str:
        return f"matter {self.matter_id} not found"


@dataclass(frozen=True, slots=True)
class UnsupportedTypeError(Exception):
    """Raised for file extensions outside PDF/DOCX/TXT/MD."""

    filename: str

    def __str__(self) -> str:
        return f"unsupported file type: {self.filename!r} (accept PDF, DOCX, TXT, MD)"


@dataclass(frozen=True, slots=True)
class OversizeError(Exception):
    """Raised when upload content exceeds the configured byte bound."""

    size: int
    limit: int

    def __str__(self) -> str:
        return f"upload of {self.size} bytes exceeds limit of {self.limit} bytes"


@dataclass(frozen=True, slots=True)
class CorruptFileError(Exception):
    """Raised when a PDF/DOCX payload cannot be parsed."""

    filename: str
    reason: str

    def __str__(self) -> str:
        return f"cannot parse {self.filename!r}: {self.reason}"


@dataclass(frozen=True, slots=True)
class OcrUnavailableError(Exception):
    """Raised when OCR cannot run: missing engine, language data, or timeout."""

    page_no: int
    reason: str = "no OCR engine installed"

    def __str__(self) -> str:
        return f"OCR unavailable for page {self.page_no}: {self.reason}"


@dataclass(frozen=True, slots=True)
class StorageError(Exception):
    """Raised when an immutable original cannot be stored without overwriting."""

    reason: str

    def __str__(self) -> str:
        return f"storage failure: {self.reason}"
