"""HTTP error mapping shared by the API routers.

Routers translate typed failures into ``(status_code, detail)`` pairs here, so
status codes and detail strings live in exactly one place.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import status

from app.domain.ingestion.errors import (
    CorruptFileError,
    MatterNotFoundError,
    OversizeError,
    StorageError,
    UnsupportedTypeError,
)


@dataclass(frozen=True, slots=True)
class NotFoundError(Exception):
    """Route-level "resource missing" signal carrying its detail string."""

    detail: str

    def __str__(self) -> str:
        return self.detail


_STATUS_BY_TYPE: dict[type[Exception], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    MatterNotFoundError: status.HTTP_404_NOT_FOUND,
    UnsupportedTypeError: status.HTTP_400_BAD_REQUEST,
    OversizeError: status.HTTP_413_CONTENT_TOO_LARGE,
    CorruptFileError: status.HTTP_400_BAD_REQUEST,
    StorageError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def map_error(exc: Exception, *, context: str | None = None) -> tuple[int, str]:
    """Map a typed failure to (HTTP status code, detail string).

    Unknown types map to 500; ``context`` prefixes the detail as
    ``"{context}: {exc}"`` when given.
    """
    status_code = _STATUS_BY_TYPE.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)
    detail = str(exc)
    if context is not None:
        detail = f"{context}: {exc}"
    return status_code, detail
