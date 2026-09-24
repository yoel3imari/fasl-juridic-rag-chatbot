"""Tests for conversation history endpoints: listing, detail with messages, and deletion."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models import Base, Conversation, Matter, Message
from app.models.base import get_db

MATTER_ID = 42


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
def client(db_session_factory):
    async def _seed() -> None:
        async with db_session_factory() as sess:
            m1 = Matter(
                id=MATTER_ID,
                title="Atlas SARL vs Employee",
                matter_type="labor",
                jurisdiction="casablanca",
                language="ar",
            )
            m2 = Matter(
                id=MATTER_ID + 1,
                title="Commercial Lease Dispute",
                matter_type="commercial",
                jurisdiction="rabat",
                language="fr",
            )
            sess.add_all([m1, m2])
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


def test_list_conversations_empty(client: TestClient) -> None:
    resp = client.get("/api/v1/conversations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_and_filter_conversations(client: TestClient, db_session_factory) -> None:
    async def _populate():
        async with db_session_factory() as sess:
            c1 = Conversation(id=1, matter_id=MATTER_ID, title="Notice period query")
            c2 = Conversation(id=2, matter_id=MATTER_ID + 1, title="Lease renewal query")
            sess.add_all([c1, c2])
            await sess.flush()

            m1 = Message(
                conversation_id=1,
                role="user",
                content="What is the statutory notice period for termination?",
            )
            m2 = Message(
                conversation_id=1,
                role="assistant",
                content="Under Article 43 of the Labor Code, notice depends on seniority.",
                citations_json=[{"domain": "authority", "source": "Code du travail"}],
            )
            m3 = Message(
                conversation_id=2,
                role="user",
                content="Can commercial lease be terminated early?",
            )
            sess.add_all([m1, m2, m3])
            await sess.commit()

    import anyio

    anyio.run(_populate)

    # 1. List all conversations
    resp = client.get("/api/v1/conversations")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    # Order by id desc
    assert items[0]["id"] == 2
    assert items[0]["matter_title"] == "Commercial Lease Dispute"
    assert items[0]["message_count"] == 1
    assert "commercial lease" in items[0]["preview"].lower()

    assert items[1]["id"] == 1
    assert items[1]["matter_title"] == "Atlas SARL vs Employee"
    assert items[1]["message_count"] == 2
    assert "statutory notice" in items[1]["preview"].lower()

    # 2. Filter by matter_id
    resp_filtered = client.get(f"/api/v1/conversations?matter_id={MATTER_ID}")
    assert resp_filtered.status_code == 200
    filtered_items = resp_filtered.json()
    assert len(filtered_items) == 1
    assert filtered_items[0]["id"] == 1


def test_get_conversation_detail(client: TestClient, db_session_factory) -> None:
    async def _populate():
        async with db_session_factory() as sess:
            c = Conversation(id=10, matter_id=MATTER_ID, title="Disciplinary hearing consultation")
            sess.add(c)
            await sess.flush()
            m1 = Message(
                conversation_id=10,
                role="user",
                content="Was Article 62 procedure respected?",
            )
            m2 = Message(
                conversation_id=10,
                role="assistant",
                content="Article 62 requires a hearing within 8 days of discovery of misconduct.",
                citations_json=[{"domain": "authority", "article_or_section": "Article 62"}],
            )
            sess.add_all([m1, m2])
            await sess.commit()

    import anyio

    anyio.run(_populate)

    resp = client.get("/api/v1/conversations/10")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == 10
    assert detail["matter_id"] == MATTER_ID
    assert detail["matter_title"] == "Atlas SARL vs Employee"
    assert detail["title"] == "Disciplinary hearing consultation"
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][0]["content"] == "Was Article 62 procedure respected?"
    assert detail["messages"][1]["role"] == "assistant"
    assert detail["messages"][1]["citations_json"] == [{"domain": "authority", "article_or_section": "Article 62"}]


def test_get_conversation_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/conversations/9999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_delete_conversation(client: TestClient, db_session_factory) -> None:
    async def _populate():
        async with db_session_factory() as sess:
            c = Conversation(id=20, matter_id=MATTER_ID, title="Temporary chat to delete")
            sess.add(c)
            await sess.flush()
            m = Message(conversation_id=20, role="user", content="Temporary message")
            sess.add(m)
            await sess.commit()

    import anyio

    anyio.run(_populate)

    resp = client.delete("/api/v1/conversations/20")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted", "id": 20}

    # Verify deleted
    resp2 = client.get("/api/v1/conversations/20")
    assert resp2.status_code == 404
