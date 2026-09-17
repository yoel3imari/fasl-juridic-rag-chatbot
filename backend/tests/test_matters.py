"""Task 10 gap-fill: matter registry create + list (backend had no way to make a matter)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models import Base
from app.models.base import get_db


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
