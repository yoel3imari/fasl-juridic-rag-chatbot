"""Analysis schemas (todo 41 extraction from app.api.v1.analysis)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

ANALYSIS_KIND = "facts-issues-gaps-risks"


class AnalysisIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = ANALYSIS_KIND


class AnalysisOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    analysis_id: int
    kind: str
    content: dict[str, Any]
