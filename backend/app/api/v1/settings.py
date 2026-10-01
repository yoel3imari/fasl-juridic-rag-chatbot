"""LLM settings persistence: last-used provider/model + API keys."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import Settings
from app.config.resolver import resolve_llm_settings, resolve_privacy_mode
from app.infrastructure.llm.agent import KNOWN_PROVIDERS
from app.repositories import settings as settings_store
from app.schemas.settings import (
    LlmSettingsResponse,
    LlmSettingsUpdate,
    LlmTestRequest,
    LlmTestResponse,
)

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
        base_urls=settings_store.base_urls(),
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


def _validate_base_urls(
    base_urls: dict[str, str | None] | None,
) -> dict[str, str | None] | None:
    if base_urls is None:
        return None
    normalized: dict[str, str | None] = {}
    for key, value in base_urls.items():
        name = key.strip().lower()
        if name not in KNOWN_PROVIDERS:
            raise HTTPException(status_code=422, detail=f"unknown provider for base URL: {key}")
        if value is None or (isinstance(value, str) and not value.strip()):
            normalized[name] = None
        elif isinstance(value, str):
            val = value.strip()
            if not (val.startswith("http://") or val.startswith("https://")):
                raise HTTPException(
                    status_code=422, detail=f"base_url must start with http:// or https://: {val}"
                )
            normalized[name] = val
        else:
            raise HTTPException(status_code=422, detail=f"invalid base_url value for: {key}")
    return normalized


@router.get("/llm", response_model=LlmSettingsResponse)
async def get_llm_settings() -> LlmSettingsResponse:
    return _build_response()


@router.put("/llm", response_model=LlmSettingsResponse)
async def put_llm_settings(body: LlmSettingsUpdate) -> LlmSettingsResponse:
    provider = _validate_provider(body.provider)
    model = _validate_model(body.model)
    api_keys = _validate_api_keys(body.api_keys)
    base_urls = _validate_base_urls(body.base_urls)
    settings_store.save_llm_settings(
        provider=provider, model=model, api_keys=api_keys, base_urls=base_urls
    )
    return _build_response()


@router.post("/llm/test", response_model=LlmTestResponse)
async def test_llm_connection(body: LlmTestRequest) -> LlmTestResponse:
    import os
    import time
    import httpx
    from app.infrastructure.llm.agent import DEFAULT_BASE_URLS

    prov = body.provider.strip().lower()
    mod = body.model.strip()
    if prov not in KNOWN_PROVIDERS:
        return LlmTestResponse(success=False, message=f"Unknown provider: {body.provider}")
    if not mod:
        return LlmTestResponse(success=False, message="Model name is required")

    api_key = (body.api_key or "").strip()
    if not api_key:
        api_key = settings_store.get_api_key(prov) or os.getenv(f"{prov.upper()}_API_KEY", "")
        if prov == "google" and not api_key:
            api_key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")

    base_url = (body.base_url or "").strip()
    if not base_url:
        base_url = (
            os.environ.get(f"{prov.upper()}_BASE_URL")
            or settings_store.get_base_url(prov)
            or DEFAULT_BASE_URLS.get(prov, "")
        )

    start = time.perf_counter()
    headers: dict[str, str] = {}
    if api_key and api_key != "no-key-required":
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if prov == "ollama":
                ollama_root = base_url.replace("/v1", "").rstrip("/")
                resp = await client.get(f"{ollama_root}/api/tags")
                latency = round((time.perf_counter() - start) * 1000, 1)
                if resp.status_code == 200:
                    return LlmTestResponse(
                        success=True,
                        message="Connected to Ollama successfully",
                        latency_ms=latency,
                    )

            models_url = f"{base_url.rstrip('/')}/models"
            resp = await client.get(models_url, headers=headers)
            latency = round((time.perf_counter() - start) * 1000, 1)
            if resp.status_code in (200, 201):
                return LlmTestResponse(
                    success=True,
                    message="Connected successfully",
                    latency_ms=latency,
                )
            elif resp.status_code == 401:
                return LlmTestResponse(
                    success=False,
                    message="Authentication failed: Invalid or missing API key (401)",
                    latency_ms=latency,
                )
            elif resp.status_code == 404:
                chat_url = f"{base_url.rstrip('/')}/chat/completions"
                chat_resp = await client.post(
                    chat_url,
                    headers=headers,
                    json={
                        "model": mod,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    },
                )
                latency = round((time.perf_counter() - start) * 1000, 1)
                if chat_resp.status_code in (200, 201):
                    return LlmTestResponse(
                        success=True,
                        message="Connected successfully (chat/completions OK)",
                        latency_ms=latency,
                    )
                return LlmTestResponse(
                    success=False,
                    message=f"Endpoint returned status {chat_resp.status_code}",
                    latency_ms=latency,
                )
            else:
                return LlmTestResponse(
                    success=False,
                    message=f"Endpoint returned status {resp.status_code}",
                    latency_ms=latency,
                )
    except httpx.ConnectError:
        return LlmTestResponse(
            success=False,
            message=f"Could not connect to {base_url}. Ensure server is running.",
        )
    except httpx.TimeoutException:
        return LlmTestResponse(
            success=False,
            message=f"Connection to {base_url} timed out after 10s.",
        )
    except Exception as exc:
        return LlmTestResponse(
            success=False,
            message=f"Connection error: {str(exc)[:120]}",
        )
