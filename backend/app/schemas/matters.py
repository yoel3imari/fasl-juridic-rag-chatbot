"""Matter-registry schemas (todo 41 extraction from app.api.v1.matters)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MatterCreateIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=255)
    matter_type: str = Field(default="general", max_length=100)
    jurisdiction: str = Field(default="casablanca", max_length=100)
    language: str = Field(default="ar", max_length=10)


class MatterOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    matter_type: str
    jurisdiction: str
    language: str
