"""Task 10 gap-fill: matter registry create + list (backend had no way to make a matter).

Extended with the matter CRUD contract: GET one, PATCH supplied-fields-only,
and DELETE with the purge (fake purger — the get_purger factory seam).
All fixtures are SYNTHETIC — never real legal text.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models import Base
from app.models.base import get_db
from app.services import matter_cleanup


@pytest.fixture()
def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    import anyio

    anyio.run(_init)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_db():
        async with factory() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    anyio.run(engine.dispose)


def _create_matter(client: TestClient) -> dict:
    """Seed one synthetic matter through the public API; return its body."""
    resp = client.post(
        "/api/v1/matters",
        json={
            "title": "Licenciement",
            "matter_type": "labor",
            "jurisdiction": "casablanca",
            "language": "ar",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class _FakePurger:
    """Network-free purger for the DELETE route (get_purger seam)."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    def delete_evidence(self, *, matter_id: int, document_id: int | None = None) -> int:
        self.calls.append((matter_id, document_id))
        return 0


def test_create_and_list_matter(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/matters",
        json={
            "title": "Licenciement",
            "matter_type": "labor",
            "jurisdiction": "casablanca",
            "language": "ar",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] > 0
    assert body["title"] == "Licenciement"

    listed = client.get("/api/v1/matters")
    assert listed.status_code == 200
    assert [m["id"] for m in listed.json()] == [body["id"]]


def test_create_matter_rejects_blank_title(client: TestClient) -> None:
    resp = client.post("/api/v1/matters", json={"title": ""})
    assert resp.status_code == 422


def test_get_matter_by_id(client: TestClient) -> None:
    """GET one matter returns the MatterOut shape; unknown id is 404."""
    created = _create_matter(client)

    resp = client.get(f"/api/v1/matters/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == created

    missing = client.get("/api/v1/matters/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "matter not found"


def test_patch_updates_only_supplied_fields(client: TestClient) -> None:
    """PATCH applies present keys only; absent keys keep their stored values."""
    created = _create_matter(client)
    matter_id = created["id"]

    resp = client.patch(f"/api/v1/matters/{matter_id}", json={"title": "Licenciement v2"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Licenciement v2"
    # Untouched fields keep their stored values — no default overwrite.
    assert body["matter_type"] == created["matter_type"]
    assert body["jurisdiction"] == created["jurisdiction"]
    assert body["language"] == created["language"]

    resp = client.patch(f"/api/v1/matters/{matter_id}", json={"language": "fr"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["language"] == "fr"
    assert body["title"] == "Licenciement v2"
    assert body["matter_type"] == "labor"

    # Empty body: valid partial update, nothing changes.
    resp = client.patch(f"/api/v1/matters/{matter_id}", json={})
    assert resp.status_code == 200
    assert resp.json() == body


def test_patch_rejects_blank_title(client: TestClient) -> None:
    """title min_length=1: a blank title is a schema-level 422."""
    created = _create_matter(client)
    resp = client.patch(f"/api/v1/matters/{created['id']}", json={"title": ""})
    assert resp.status_code == 422


def test_patch_unknown_matter_404(client: TestClient) -> None:
    resp = client.patch("/api/v1/matters/999999", json={"title": "Nope"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "matter not found"


def test_delete_matter_returns_counts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """DELETE an empty matter: exact bare-dict body, then 404 on re-read."""
    created = _create_matter(client)
    matter_id = created["id"]
    fake = _FakePurger()
    monkeypatch.setattr(matter_cleanup, "get_purger", lambda: fake)

    resp = client.delete(f"/api/v1/matters/{matter_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "status": "deleted",
        "id": matter_id,
        "removed_documents": 0,
        "removed_points": 0,
        "removed_files": 0,
    }
    assert fake.calls == [(matter_id, None)]

    assert client.get(f"/api/v1/matters/{matter_id}").status_code == 404
    assert [m["id"] for m in client.get("/api/v1/matters").json()] == []


def test_delete_unknown_matter_404(client: TestClient) -> None:
    resp = client.delete("/api/v1/matters/999999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "matter not found"
