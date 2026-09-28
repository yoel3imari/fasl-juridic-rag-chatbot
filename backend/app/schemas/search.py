"""Search request schemas (todo 41 extraction from app.api.v1.search)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Domain = Literal["matter", "authority", "both"]


class SearchBody(BaseModel):
    query: str = Field(min_length=1)
    domain: Domain = "both"
    matter_id: int | None = None
    top_k: int = Field(default=5, ge=1, le=30)
