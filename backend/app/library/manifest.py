from enum import Enum
from typing import Optional

from pydantic import BaseModel, field_validator


class EditionType(str, Enum):
    AR_GENERAL = "ar-general"
    FR_TRANSLATION = "fr-translation"


class ManifestEntry(BaseModel):
    source: str
    version: str
    edition: EditionType
    file_path: str
    pub_date: Optional[str] = None
    doc_date: Optional[str] = None
    hijri_date: Optional[str] = None
    language: Optional[str] = None
    coverage_note: Optional[str] = None

    @field_validator("source")
    @classmethod
    def source_must_not_be_none(cls, v):
        if v is None:
            raise ValueError("source must not be None")
        return v

    @field_validator("version")
    @classmethod
    def version_must_not_be_none(cls, v):
        if v is None:
            raise ValueError("version must not be None")
        return v


class LibraryManifest(BaseModel):
    entries: list[ManifestEntry]
