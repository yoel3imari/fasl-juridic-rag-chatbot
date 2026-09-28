"""LLM settings persistence: last-used provider/model + API keys."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import Settings
from app.config.resolver import resolve_llm_settings, resolve_privacy_mode
from app.infrastructure.llm.agent import KNOWN_PROVIDERS
from app.repositories import settings as settings_store
from app.schemas.settings import LlmSettingsResponse, LlmSettingsUpdate

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


def _resolve_current() -> tuple[str, str, str]:
    env = Settings()  # type: ignore[call-arg]
    resolved = resolve_llm_settings(env)
    return resolved.provider, resolved.model, resolve_privacy_mode(env)


def _build_response() -> LlmSettingsResponse:
    provider, model, privacy_mode = _resolve_current()
    return LlmSettingsResponse(
        current_provider=provider,
        current_model=model,
        privacy_mode=privacy_mode,
        keys_status=settings_store.keys_status(),
        masked_keys=settings_store.masked_keys(),
    )


def _validate_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    name = provider.strip().lower()
    if name not in KNOWN_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"unknown provider: {provider}")
    return name



def _validate_model(model: str | None) -> str | None:
    if model is None:
        return None
    name = model.strip()
    if not name or any(ch.isspace() for ch in name):
        raise HTTPException(
            status_code=422, detail="model must be non-empty with no whitespace"
        )
    return name


def _validate_api_keys(
    api_keys: dict[str, str | None] | None,
) -> dict[str, str | None] | None:
    if api_keys is None:
        return None
    normalized: dict[str, str | None] = {}
    for key, value in api_keys.items():
        name = key.strip().lower()
        if name not in settings_store.SUPPORTED_KEY_PROVIDERS:
            raise HTTPException(status_code=422, detail=f"unknown key provider: {key}")
        if value is None or (isinstance(value, str) and value == ""):
            normalized[name] = None
        elif isinstance(value, str):
            normalized[name] = value
        else:
            raise HTTPException(status_code=422, detail=f"invalid key value for: {key}")
    return normalized


@router.get("/llm", response_model=LlmSettingsResponse)
async def get_llm_settings() -> LlmSettingsResponse:
    return _build_response()


@router.put("/llm", response_model=LlmSettingsResponse)
async def put_llm_settings(body: LlmSettingsUpdate) -> LlmSettingsResponse:
    provider = _validate_provider(body.provider)
    model = _validate_model(body.model)
    api_keys = _validate_api_keys(body.api_keys)
    settings_store.save_llm_settings(provider=provider, model=model, api_keys=api_keys)
    return _build_response()
