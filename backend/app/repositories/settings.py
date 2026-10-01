"""File-based persistence for last-used LLM provider/model + API keys.

File-based (NOT DB) so it works before migrations run. JSON file at
``<STORAGE_DIR>/llm_settings.json`` with 0o600 permissions. Missing or
corrupt files degrade gracefully to defaults.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.domain.privacy import EXTERNAL_PROVIDER_IDS

SUPPORTED_KEY_PROVIDERS: tuple[str, ...] = EXTERNAL_PROVIDER_IDS


def get_settings_path() -> Path:
    """Resolve the JSON settings file path.

    Respects ``FASL_STORAGE_DIR`` then ``STORAGE_DIR`` env, falling back to
    the configured ``settings.STORAGE_DIR`` and finally ``./storage``.
    """
    storage_dir = os.getenv("FASL_STORAGE_DIR") or os.getenv("STORAGE_DIR")
    if storage_dir is None:
        try:
            from app.config import settings

            storage_dir = settings.STORAGE_DIR
        except Exception:
            storage_dir = "./storage"
    return Path(storage_dir) / "llm_settings.json"


def _defaults() -> dict[str, Any]:
    return {
        "provider": None,
        "model": None,
        "base_urls": {},
        "api_keys": {p: "" for p in SUPPORTED_KEY_PROVIDERS},
    }


def _normalize_loaded(data: Any) -> dict[str, Any]:
    """Merge raw JSON into the canonical shape; tolerate corrupt input."""
    base = _defaults()
    if not isinstance(data, dict):
        return base
    provider = data.get("provider")
    base["provider"] = provider if isinstance(provider, str) and provider else None
    model = data.get("model")
    base["model"] = model if isinstance(model, str) and model else None
    raw_keys = data.get("api_keys")
    if isinstance(raw_keys, dict):
        for p in SUPPORTED_KEY_PROVIDERS:
            val = raw_keys.get(p)
            base["api_keys"][p] = val if isinstance(val, str) else ""
    raw_urls = data.get("base_urls")
    if isinstance(raw_urls, dict):
        for k, v in raw_urls.items():
            if isinstance(k, str) and isinstance(v, str):
                base["base_urls"][k.strip().lower()] = v.strip()
    return base


def load_llm_settings() -> dict[str, Any]:
    """Load persisted settings; return defaults on missing/corrupt file."""
    path = get_settings_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, OSError):
        return _defaults()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return _defaults()
    return _normalize_loaded(data)


def save_llm_settings(
    provider: str | None = None,
    model: str | None = None,
    api_keys: dict[str, str | None] | None = None,
    base_urls: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Merge the given fields into the stored settings and persist atomically.

    ``api_keys`` values of ``None`` or ``""`` delete the stored key.
    Returns the merged settings dict.
    """
    current = load_llm_settings()
    if provider is not None:
        current["provider"] = provider
    if model is not None:
        current["model"] = model
    if api_keys is not None:
        for key, value in api_keys.items():
            name = key.strip().lower()
            if name not in SUPPORTED_KEY_PROVIDERS:
                continue
            if value is None or (isinstance(value, str) and value == ""):
                current["api_keys"][name] = ""
            elif isinstance(value, str):
                current["api_keys"][name] = value
    if base_urls is not None:
        if "base_urls" not in current or not isinstance(current["base_urls"], dict):
            current["base_urls"] = {}
        for key, value in base_urls.items():
            name = key.strip().lower()
            if value is None or (isinstance(value, str) and not value.strip()):
                current["base_urls"].pop(name, None)
            elif isinstance(value, str):
                current["base_urls"][name] = value.strip()
    _atomic_write(current)
    return current


def get_base_url(provider: str) -> str | None:
    """Return the stored custom base URL for a provider, or None."""
    stored = load_llm_settings()
    urls = stored.get("base_urls")
    if isinstance(urls, dict):
        val = urls.get(provider.strip().lower())
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def base_urls() -> dict[str, str | None]:
    """Map of provider -> custom base URL."""
    stored = load_llm_settings()
    urls = stored.get("base_urls")
    if isinstance(urls, dict):
        return {k: v for k, v in urls.items() if isinstance(v, str)}
    return {}


def _atomic_write(data: dict[str, Any]) -> None:
    """Write JSON via tmp file + rename; ensure parent dir and 0o600 perms."""
    path = get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".llm_settings_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def get_api_key(provider: str) -> str:
    """Return the stored key for a provider, or ``""`` when absent."""
    name = provider.strip().lower()
    stored = load_llm_settings()
    keys = stored.get("api_keys")
    if not isinstance(keys, dict):
        return ""
    value = keys.get(name, "")
    return value if isinstance(value, str) else ""


def has_api_key(provider: str) -> bool:
    """True when a non-empty key is stored for the provider."""
    return bool(get_api_key(provider))


def mask_key(value: str | None) -> str | None:
    """Mask a key for display: ``None``/empty → ``None``, else last 4 chars."""
    if not value:
        return None
    tail = value[-4:] if len(value) >= 4 else value
    return f"sk-...{tail}"


def keys_status() -> dict[str, bool]:
    """Map of provider → whether a key is stored."""
    stored = load_llm_settings()
    keys = stored.get("api_keys")
    if not isinstance(keys, dict):
        keys = {}
    return {
        p: bool(keys.get(p)) if isinstance(keys.get(p), str) else False
        for p in SUPPORTED_KEY_PROVIDERS
    }


def masked_keys() -> dict[str, str | None]:
    """Map of provider → masked key (or ``None`` when absent)."""
    stored = load_llm_settings()
    keys = stored.get("api_keys")
    if not isinstance(keys, dict):
        keys = {}
    out: dict[str, str | None] = {}
    for p in SUPPORTED_KEY_PROVIDERS:
        val = keys.get(p, "")
        out[p] = mask_key(val) if isinstance(val, str) else None
    return out
