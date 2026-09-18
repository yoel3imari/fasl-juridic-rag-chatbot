"""Task 11 TDD: Docker Compose dev/prod stacks declare + run all services.

Static part (always runs): docker-compose.dev.yml and docker-compose.prod.yml
exist at repo root and declare the four required services with the required
published ports and no forbidden ones. Prod additionally runs frontend/backend
non-root, pins base images, and wires the frontend API base URL via build args.
Live part (skips when the stack is down): every service answers its health
endpoint. Run `make dev` (or docker compose -f docker-compose.dev.yml up -d)
before expecting live pass.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[1]
DEV_COMPOSE = REPO / "docker-compose.dev.yml"
PROD_COMPOSE = REPO / "docker-compose.prod.yml"

REQUIRED_SERVICES = {
    "fastapi": 8000,
    "nextjs": 3000,
    "qdrant": 6333,
    "crispembed": 8080,
}

FORBIDDEN = ("postgres", "redis", "ollama", "auth")

DOCKERFILES = {
    "backend": REPO / "backend" / "Dockerfile",
    "frontend": REPO / "frontend" / "Dockerfile",
    "qdrant": REPO / "qdrant" / "Dockerfile",
    "crispembed": REPO / "crispembed" / "Dockerfile",
}


def _load_compose(path: Path) -> dict:
    assert path.exists(), f"{path.name} missing — run task 11"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _published_ports(service: dict) -> list[str]:
    return [str(p) for p in service.get("ports", [])]


def test_all_services_declared() -> None:
    """Dev and prod compose files declare fastapi/nextjs/qdrant/crispembed."""
    dev = _load_compose(DEV_COMPOSE)
    prod = _load_compose(PROD_COMPOSE)
    dev_services = dev.get("services", {})
    prod_services = prod.get("services", {})
    for name, port in REQUIRED_SERVICES.items():
        assert name in dev_services, f"service {name!r} missing from dev compose"
        assert name in prod_services, f"service {name!r} missing from prod compose"
    for name, port in REQUIRED_SERVICES.items():
        ports = _published_ports(dev_services[name])
        assert any(str(port) in p for p in ports), f"dev {name} lacks port {port}"
    for name, port in (("fastapi", 8000), ("nextjs", 3000)):
        ports = _published_ports(prod_services[name])
        assert any(str(port) in p for p in ports), f"prod {name} lacks port {port}"
    prod_blob_ports = " ".join(
        p for s in prod_services.values() for p in _published_ports(s)
    )
    assert "6333" not in prod_blob_ports, "prod must not publish qdrant 6333"
    assert "8080" not in prod_blob_ports, "prod must not publish crispembed 8080"


def test_no_forbidden_services() -> None:
    """No PostgreSQL, Redis, Ollama, or auth services (plan scope)."""
    blob = ""
    for path in (DEV_COMPOSE, PROD_COMPOSE):
        blob += yaml.safe_dump(_load_compose(path)).lower()
    for bad in FORBIDDEN:
        assert bad not in blob, f"forbidden service {bad!r} in compose"


def test_pinned_base_images() -> None:
    """Dockerfiles pin base images (no :latest); crispembed pins git SHA/tag."""
    for name, path in DOCKERFILES.items():
        assert path.exists(), f"{path} missing — run task 11"
        text = path.read_text(encoding="utf-8")
        assert ":latest" not in text.lower(), f"{name} Dockerfile uses :latest"
    crisp = DOCKERFILES["crispembed"].read_text(encoding="utf-8")
    crisp_lower = crisp.lower()
    assert "depth 1 https" not in crisp_lower, (
        "crispembed Dockerfile must not clone moving HEAD"
    )
    has_checkout = "git checkout" in crisp_lower
    has_tag = re.search(r"v?\d+\.\d+\.\d+", crisp) is not None
    assert has_checkout or has_tag, (
        "crispembed Dockerfile must pin git SHA via 'git checkout' or a tag"
    )


def test_prod_runs_nonroot() -> None:
    """Prod fastapi/nextjs/crispembed run as non-root with hardened options."""
    compose = _load_compose(PROD_COMPOSE)
    services = compose.get("services", {})
    for name in ("fastapi", "nextjs", "crispembed"):
        assert name in services, f"service {name!r} missing from prod compose"
        svc = services[name]
        assert "user" in svc, f"prod {name} must set 'user'"
        sec_opt = str(svc.get("security_opt", "")).lower()
        read_only = svc.get("read_only", False) is True
        assert ("no-new-privileges" in sec_opt) or read_only, (
            f"prod {name} must set security_opt no-new-privileges or read_only"
        )
    for name in ("fastapi", "nextjs"):
        assert "cap_drop" in services[name], f"prod {name} must set 'cap_drop'"


def test_prod_frontend_api_build_arg() -> None:
    """Prod nextjs service passes NEXT_PUBLIC_API_BASE_URL via build.args."""
    compose = _load_compose(PROD_COMPOSE)
    services = compose.get("services", {})
    assert "nextjs" in services, "service 'nextjs' missing from prod compose"
    build = services["nextjs"].get("build", {})
    assert isinstance(build, dict), "prod nextjs must use build dict with args"
    args = build.get("args", {})
    if isinstance(args, dict):
        assert "NEXT_PUBLIC_API_BASE_URL" in args, (
            "prod nextjs build.args must contain NEXT_PUBLIC_API_BASE_URL"
        )
    else:
        blob = " ".join(str(a) for a in args)
        assert "NEXT_PUBLIC_API_BASE_URL" in blob, (
            "prod nextjs build.args must contain NEXT_PUBLIC_API_BASE_URL"
        )


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
