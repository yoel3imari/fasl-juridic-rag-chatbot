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
    base_urls: dict[str, str | None] = Field(default_factory=dict)


class LlmSettingsUpdate(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str | None = None
    model: str | None = None
    api_keys: dict[str, str | None] | None = Field(default=None)
    base_urls: dict[str, str | None] | None = Field(default=None)


class LlmTestRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None


class LlmTestResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    success: bool
    message: str
    latency_ms: float | None = None
