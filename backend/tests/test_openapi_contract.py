"""Contract (characterization) tests for the served HTTP surface.

Why this file exists
--------------------
Plan todo 6. The OpenAPI **schema alone does not enforce the owner's freeze**.
Three classes of contract are invisible to it and are pinned here separately:

1. **The schema itself** — a committed byte-for-byte snapshot of
   `app.openapi()`, so any added/removed route, response field, status code or
   `response_model` is a diff, not a subtlety.
2. **Untyped runtime payloads** — several endpoints return a bare `dict`, which
   renders in the schema as an untyped object: `GET /api/v1/library/coverage`,
   `POST|GET /api/v1/search`, `DELETE /api/v1/conversations/{id}` and
   `GET /health`. Their key order and truncation semantics are therefore
   unverified by the schema and are asserted here explicitly.
3. **`HTTPException` detail strings** — never present in the schema. Todo 35
   centralises error→status mapping; the table below is what that refactor must
   reproduce exactly (`chat.py` "conversation not found in this matter" vs
   `conversations.py` "conversation not found"; the `" — retry shortly"` suffix
   on search 503s).

The snapshot is generated, never hand-edited: regenerating it is itself a
contract change and must go through the plan.

All fixtures below are SYNTHETIC — never real legal text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import library as libmod
from app.main import app
from app.models import Base, Matter
from app.models.base import get_db

SNAPSHOT_PATH = Path(__file__).parent / "openapi_snapshot.json"

MATTER_ID = 7


# --------------------------------------------------------------------------- #
# part (i) — the committed OpenAPI schema snapshot
# --------------------------------------------------------------------------- #
def _normalized_schema() -> str:
    """Sorted-key, 2-space-indented, trailing-newline JSON of the live schema."""
    return json.dumps(app.openapi(), sort_keys=True, indent=2) + "\n"


def test_openapi_schema_matches_snapshot() -> None:
    """The served schema is byte-identical to the committed baseline."""
    actual = _normalized_schema()
    expected = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert actual == expected, (
        "OpenAPI schema drifted from tests/openapi_snapshot.json. "
        "A schema change is a contract change: update the plan, not the snapshot."
    )


def test_openapi_snapshot_is_sorted_and_newline_terminated() -> None:
    """Guards the snapshot format itself, so a hand-edit cannot slip in."""
    raw = SNAPSHOT_PATH.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert raw == json.dumps(parsed, sort_keys=True, indent=2) + "\n"


def test_every_route_is_represented_in_the_schema() -> None:
    """Every served path appears in the snapshot — catches an un-routed router."""
    schema_paths = set(app.openapi()["paths"])
    served = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str) and not path.startswith(("/openapi", "/docs", "/redoc")):
            served.add(path)
    assert served - schema_paths == set(), f"routed but not in schema: {served - schema_paths}"


# --------------------------------------------------------------------------- #
# part (ii) — untyped runtime payloads
# --------------------------------------------------------------------------- #
@pytest.fixture()
def db_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    import anyio

    anyio.run(_init)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    anyio.run(engine.dispose)


@pytest.fixture()
def client(db_session_factory, monkeypatch: pytest.MonkeyPatch):
    async def _seed() -> None:
        async with db_session_factory() as sess:
            sess.add(
                Matter(
                    id=MATTER_ID,
                    title="Synthetic matter",
                    matter_type="labor",
                    jurisdiction="casablanca",
                    language="ar",
                )
            )
            await sess.commit()

    import anyio

    anyio.run(_seed)

    async def _override_db():
        async with db_session_factory() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_payload_key_order() -> None:
    """`GET /health` returns a bare dict: key order is the contract."""
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert list(resp.json().keys()) == ["status", "library_version"]


def test_chat_models_provider_ids_and_order() -> None:
    """`GET /api/v1/chat/models` provider id list is frozen (todo 30 moves it)."""
    with TestClient(app) as client:
        resp = client.get("/api/v1/chat/models")
    assert resp.status_code == 200
    body = resp.json()
    assert list(body.keys()) == [
        "current_provider",
        "current_model",
        "privacy_mode",
        "providers",
    ]
    # The 165-line hardcoded catalog that todo 30 extracts into app/schemas/chat.py.
    # Values are frozen: order, ids, names, locality flags, defaults and model ids.
    assert [p["id"] for p in body["providers"]] == [
        "ollama",
        "openrouter",
        "openai",
        "anthropic",
        "google",
        "groq",
    ]
    assert [p["name"] for p in body["providers"]] == [
        "Ollama (Local)",
        "OpenRouter (Unified Cloud)",
        "OpenAI",
        "Anthropic",
        "Google Gemini",
        "Groq",
    ]
    assert [p["is_external"] for p in body["providers"]] == [
        False,
        True,
        True,
        True,
        True,
        True,
    ]
    assert [p["default_model"] for p in body["providers"]] == [
        "llama3.2",
        "anthropic/claude-3.5-sonnet",
        "gpt-4o",
        "claude-3-5-sonnet-latest",
        "gemini-2.0-flash",
        "llama-3.3-70b-versatile",
    ]
    assert [len(p["models"]) for p in body["providers"]] == [5, 5, 3, 2, 2, 2]
    assert body["providers"][0]["models"][0]["id"] == "llama3.2"
    assert body["providers"][0]["models"][0]["recommended"] is True
    assert [m["id"] for m in body["providers"][0]["models"] if m["recommended"]] == [
        "llama3.2",
        "qwen2.5",
    ]


def test_coverage_zero_summary_shape_and_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `_zero_summary` degradation payload is key-order sensitive.

    `_zero_summary()`/`_zero_totals()` in api/v1/library.py define the exact key
    order; a refactor that reorders or drops a key silently changes the JSON the
    frontend renders.
    """
    monkeypatch.setattr(libmod, "MANIFEST_PATH", Path("/nonexistent/no-manifest.json"))
    monkeypatch.setattr(libmod, "SEED_STATE_PATH", Path("/nonexistent/no-seed.json"))
    monkeypatch.setattr(libmod.settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    resp = client.get("/api/v1/library/coverage")
    assert resp.status_code == 200
    body = resp.json()
    assert list(body.keys()) == [
        "titles",
        "gaps",
        "library_version",
        "summary",
        "titles_truncated",
        "gaps_truncated",
    ]
    assert list(body["summary"].keys()) == [
        "totals",
        "by_category",
        "by_status",
        "by_edition",
    ]
    assert list(body["summary"]["totals"].keys()) == [
        "files",
        "indexed",
        "extracted",
        "embedded",
        "chunks",
        "chunks_indexed",
    ]


def test_coverage_ledger_missing_gap_text(client: TestClient, monkeypatch) -> None:
    """`LEDGER_MISSING_GAP` is a literal the frontend matches on."""
    from app.api.v1.library import LEDGER_MISSING_GAP

    monkeypatch.setattr(libmod, "MANIFEST_PATH", Path("/nonexistent/no-manifest.json"))
    monkeypatch.setattr(libmod, "SEED_STATE_PATH", Path("/nonexistent/no-seed.json"))
    # A file-backed sqlite URL whose file does not exist -> ledger unreadable.
    monkeypatch.setattr(
        libmod.settings, "DATABASE_URL", "sqlite+aiosqlite:///nonexistent/ledger.db"
    )
    resp = client.get("/api/v1/library/coverage")
    assert resp.status_code == 200
    body = resp.json()
    assert LEDGER_MISSING_GAP in body["gaps"], body["gaps"]
    assert body["summary"]["totals"]["files"] == 0


def test_coverage_truncation_semantics(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`COVERAGE_LIST_CAP` bounds the lists; the overflow is only counted."""
    from app.api.v1.library import COVERAGE_LIST_CAP

    cap = COVERAGE_LIST_CAP
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "source": f"SYN-SRC-{i}",
                        "version": "v0-test",
                        "edition": "ar-general",
                        "language": "ar",
                    }
                    for i in range(cap + 3)
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(libmod, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(libmod, "SEED_STATE_PATH", tmp_path / "no-seed.json")
    monkeypatch.setattr(libmod.settings, "DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    resp = client.get("/api/v1/library/coverage")
    body = resp.json()
    assert len(body["titles"]) == cap
    assert body["titles_truncated"] == 3
    # One extra gap: every unseeded title is a gap, and the memory-db ledger is
    # not file-backed so no ledger gap is appended here.
    assert len(body["gaps"]) == cap
    assert body["gaps_truncated"] == 4
    assert body["gaps"][0] == "not yet seeded: SYN-SRC-0@v0-test#ar-general"


def test_delete_conversation_returns_bare_dict(client: TestClient, db_session_factory) -> None:
    """`DELETE /api/v1/conversations/{id}` is untyped; assert its literal body."""
    import anyio

    from app.models import Conversation

    async def _seed_conv() -> int:
        async with db_session_factory() as sess:
            conv = Conversation(matter_id=MATTER_ID, title="Synthetic conv")
            sess.add(conv)
            await sess.commit()
            return conv.id

    conv_id = anyio.run(_seed_conv)
    resp = client.delete(f"/api/v1/conversations/{conv_id}")
    assert resp.status_code == 200, resp.text
    # Bare-dict response: neither the keys nor their order appear in the schema.
    assert list(resp.json().keys()) == ["status", "id"]
    assert resp.json() == {"status": "deleted", "id": conv_id}


# --------------------------------------------------------------------------- #
# part (iii) — (endpoint, status_code, detail) table
# --------------------------------------------------------------------------- #
# Every pair below is one that todo 35's central error-mapping refactor touches.
# The detail strings are asserted exactly, including the em-dash suffix.
@pytest.mark.parametrize(
    ("method", "path", "status_code", "detail", "body"),
    [
        # chat.py:338 — 404 when the conversation is absent or belongs to another
        # matter. Note the distinct wording vs conversations.py:134.
        (
            "POST",
            "/api/v1/chat",
            404,
            "conversation not found in this matter",
            {"matter_id": MATTER_ID, "content": "x", "conversation_id": 999999},
        ),
        # conversations.py:134 / :169 — 404, different wording.
        (
            "GET",
            "/api/v1/conversations/999999",
            404,
            "conversation not found",
            {},
        ),
        (
            "DELETE",
            "/api/v1/conversations/999999",
            404,
            "conversation not found",
            {},
        ),
        # analysis.py:60 — 404 matter missing.
        (
            "POST",
            "/api/v1/matters/999999/analysis",
            404,
            "matter not found",
            {},
        ),
        # search.py:47 — matter-domain search without matter_id.
        (
            "POST",
            "/api/v1/search",
            422,
            "matter_id is required for matter-domain search",
            {"domain": "matter", "query": "x"},
        ),
        (
            "GET",
            "/api/v1/search",
            422,
            "matter_id is required for matter-domain search",
            None,
        ),
        # settings.py:57 / :68 / :82 / :88 — provider, model and key validation.
        # All four raise from PUT /settings/llm: the api_keys map is validated in
        # the same handler (_validate_api_keys), there is no per-key route.
        (
            "PUT",
            "/api/v1/settings/llm",
            422,
            "unknown provider: definitely-not-a-provider",
            {"provider": "definitely-not-a-provider", "model": "m"},
        ),
        (
            "PUT",
            "/api/v1/settings/llm",
            422,
            "model must be non-empty with no whitespace",
            {"provider": "ollama", "model": "   "},
        ),
        (
            "PUT",
            "/api/v1/settings/llm",
            422,
            "unknown key provider: Definitely-Not-A-Provider",
            {
                "provider": "ollama",
                "model": "llama3.2",
                "api_keys": {"Definitely-Not-A-Provider": "sk-synthetic"},
            },
        ),
    ],
)
def test_error_mapping_table(
    client: TestClient,
    method: str,
    path: str,
    status_code: int,
    detail: str,
    body: dict[str, Any] | None,
) -> None:
    """Pin (endpoint, status, detail) exactly — the todo 35 contract."""
    url = path
    if method == "GET" and body is None and path == "/api/v1/search":
        url = "/api/v1/search?query=x&domain=matter"
    resp = (
        client.request(method, url, json=body) if body is not None else client.request(method, url)
    )
    assert resp.status_code == status_code, (method, path, resp.status_code, resp.text)
    payload = resp.json()
    assert payload["detail"] == detail, (method, path, payload)


def test_api_key_value_type_is_rejected_by_the_schema(client: TestClient) -> None:
    """A non-string api_key value is a schema-level 422, not the handler's.

    `_validate_api_keys` (settings.py:88) has a defensive `invalid key value`
    branch, but the Pydantic model rejects the wrong type before the handler
    runs, so that detail string is unreachable over HTTP. Pinned here so a
    future model change that lets it through is caught.
    """
    resp = client.put(
        "/api/v1/settings/llm",
        json={
            "provider": "ollama",
            "model": "llama3.2",
            "api_keys": {"openai": 12345},
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list)
    assert detail[0]["loc"] == ["body", "api_keys", "openai"]


def test_search_503_detail_suffix_is_preserved(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`search.py:78` appends " — retry shortly" (em dash) to the 503 detail.

    A refactor that centralises the mapping must keep the suffix byte-exact.
    """
    from app.api import deps as deps_mod

    class _BoomEmbedder:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            from app.services import search as svc

            raise svc.EmbeddingUnavailableError("synthetic embedder outage")

    monkeypatch.setattr(deps_mod, "get_embedder", lambda: _BoomEmbedder())
    from app.services import search as svc_mod

    svc_mod.clear_search_cache()
    monkeypatch.setenv("MATTER_PRIVACY_MODE", "strict")

    resp = client.get("/api/v1/search?query=x&domain=authority")
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert detail.endswith(" — retry shortly"), repr(detail)
    assert "synthetic embedder outage" in detail
