"""LLM-settings schemas (todo 41 extraction from app.api.v1.settings)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LlmSettingsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_provider: str
    current_model: str
    privacy_mode: str
    keys_status: dict[str, bool]
    masked_keys: dict[str, str | None]


class LlmSettingsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str | None = None
    model: str | None = None
    api_keys: dict[str, str | None] | None = Field(default=None)
