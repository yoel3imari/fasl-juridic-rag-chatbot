"""Task 11 TDD: Docker Compose local stack declares + runs all services.

Static part (always runs): docker-compose.yml exists and declares the four
required services with the required published ports and no forbidden ones.
Live part (skips when the stack is down): every service answers its health
endpoint. Run `make dev` (or docker compose up -d) before expecting live pass.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[1]
COMPOSE = REPO / "docker-compose.yml"

REQUIRED_SERVICES = {
    "fastapi": 8000,
    "nextjs": 3000,
    "qdrant": 6333,
    "crispembed": 8080,
}

FORBIDDEN = ("postgres", "redis", "ollama", "auth")


def _load_compose() -> dict:
    assert COMPOSE.exists(), "docker-compose.yml missing — run task 11"
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_all_services_declared() -> None:
    """Compose file declares fastapi/nextjs/qdrant/crispembed with ports."""
    compose = _load_compose()
    services = compose.get("services", {})
    for name, port in REQUIRED_SERVICES.items():
        assert name in services, f"service {name!r} missing from compose"
        ports = [str(p) for p in services[name].get("ports", [])]
        assert any(str(port) in p for p in ports), f"{name} lacks port {port}"


def test_no_forbidden_services() -> None:
    """No PostgreSQL, Redis, Ollama, or auth services (plan scope)."""
    compose = _load_compose()
    blob = yaml.safe_dump(compose).lower()
    for bad in FORBIDDEN:
        assert bad not in blob, f"forbidden service {bad!r} in compose"


def _probe(url: str, timeout: float = 3.0) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


@pytest.mark.parametrize(
    "name,url",
    [
        ("fastapi", "http://localhost:8000/health"),
        ("nextjs", "http://localhost:3000/"),
        ("qdrant", "http://localhost:6333/"),
        ("crispembed", "http://localhost:8080/health"),
    ],
)
def test_all_services_healthy(name: str, url: str) -> None:
    """Live: each service answers its health endpoint (skip if stack down)."""
    if os.getenv("FASL_LIVE_COMPOSE") != "1":
        pytest.skip("set FASL_LIVE_COMPOSE=1 with the stack up for live check")
    assert _probe(url), f"{name} unhealthy at {url}"
