"""TDD (red-first): drafting assistance with explicit review states (task 9).

Given: POST /api/v1/matters/{matter_id}/drafts builds from the latest
       Analysis content_json (issues/gaps/obligations with span refs) plus
       authority citations already stored in the matter (message
       citations_json) — never fabricated.
When: a draft is created and transitioned draft→acknowledged or
      draft→lawyer_reviewed.
Then: every draft carries the provisional banner + citations; the
      acknowledged flow NEVER renders the string "lawyer review" (negative
      snapshot over the full response JSON); the lawyer flow records
      reviewer + timestamp; unknown state transitions → 400.

All fixtures below are SYNTHETIC test data — never real legal text.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models import (
    Base,
    Conversation,
    Document,
    DocumentSection,
    Matter,
    Message,
)
from app.models.base import get_db

PROVISIONAL_BANNER = "AI-generated draft, not legal advice, verify before use"

LETTER_TEXT = (
    "Termination letter. Mr. Karim Bennani, your employment contract is terminated "
    "effective 2024-03-15. Your notice period is 1 month. Final compensation "
    "of 15000 MAD will be paid. Signed by Societe Atlas SARL."
)

AUTHORITY_CITATION = {
    "domain": "authority",
    "source": "synthetic-labour-code",
    "version": "v0-test",
    "edition": "ar-general",
    "pub_date": "2024-01-01",
    "doc_date": "2024-01-01",
    "language": "ar",
    "article_or_section": "Art. 999 (synthetic)",
}


def _section(
    doc_id: int,
    matter_id: int,
    text: str,
    title: str = "Section 1",
    section_id: str = "sec-1",
    page: int = 1,
) -> DocumentSection:
    return DocumentSection(
        document_id=doc_id,
        matter_id=matter_id,
        version_no=1,
        section_id=section_id,
        parent_section_id=None,
        title=title,
        page_start=page,
        page_end=page,
        span_start=0,
        span_end=len(text),
        faithful_text=text,
        normalized_text=text,
        ocr_confidence=None,
        needs_review=False,
    )


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
    async def _override_db():
        async with db_session_factory() as sess:
            yield sess

    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setenv("MATTER_PRIVACY_MODE", "strict")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "llama3.2")
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _make_matter_with_evidence(
    db_session_factory, *, with_authority: bool = True
) -> int:
    import anyio

    async def _seed() -> int:
        async with db_session_factory() as sess:
            matter = Matter(
                title="Synthetic labor matter",
                matter_type="labor",
                jurisdiction="casablanca",
                language="ar",
            )
            sess.add(matter)
            await sess.flush()
            doc = Document(
                matter_id=matter.id,
                filename="letter.txt",
                original_name="Termination_Letter.txt",
                mime_type="text/plain",
                doc_type="letter",
                status="indexed",
                chunk_count=1,
            )
            sess.add(doc)
            await sess.flush()
            sess.add(_section(doc.id, matter.id, LETTER_TEXT))
            if with_authority:
                conv = Conversation(matter_id=matter.id, title="chat")
                sess.add(conv)
                await sess.flush()
                sess.add(
                    Message(
                        conversation_id=conv.id,
                        role="assistant",
                        content="synthetic grounded answer",
                        citations_json=[AUTHORITY_CITATION],
                    )
                )
            await sess.commit()
            return matter.id

    return anyio.run(_seed)


def _make_empty_matter(db_session_factory) -> int:
    import anyio

    async def _seed() -> int:
        async with db_session_factory() as sess:
            matter = Matter(
                title="Empty matter",
                matter_type="labor",
                jurisdiction="rabat",
                language="ar",
            )
            sess.add(matter)
            await sess.commit()
            return matter.id

    return anyio.run(_seed)


def _make_analysed_matter(client: TestClient, db_session_factory) -> int:
    matter_id = _make_matter_with_evidence(db_session_factory)
    resp = client.post(f"/api/v1/matters/{matter_id}/analysis", json={})
    assert resp.status_code == 200, resp.text
    return matter_id


def _create_draft(client: TestClient, matter_id: int, draft_type: str = "memo") -> dict:
    resp = client.post(
        f"/api/v1/matters/{matter_id}/drafts", json={"draft_type": draft_type}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_acknowledged_never_renders_lawyer_review(
    client: TestClient, db_session_factory
) -> None:
    """Regular-user acknowledgement must never display 'lawyer review'."""
    from app.drafts import build_draft  # drafting engine module exists

    assert build_draft
    matter_id = _make_analysed_matter(client, db_session_factory)
    created = _create_draft(client, matter_id)
    assert "lawyer review" not in json.dumps(created).lower()
    resp = client.post(f"/api/v1/drafts/{created['draft_id']}/acknowledge")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review_state"] == "acknowledged"
    assert "lawyer review" not in json.dumps(body).lower()


def test_create_draft_returns_banner_and_citations(
    client: TestClient, db_session_factory
) -> None:
    """Draft creation returns the provisional banner + grounded citations."""
    matter_id = _make_analysed_matter(client, db_session_factory)
    body = _create_draft(client, matter_id, draft_type="demand_letter")
    assert body["matter_id"] == matter_id
    assert body["draft_type"] == "demand_letter"
    assert body["review_state"] == "draft"
    assert body["provisional_banner"] == PROVISIONAL_BANNER
    assert body["provisional_banner_ar"].strip()
    assert body["provisional_banner_fr"].strip()
    assert PROVISIONAL_BANNER in body["content"]
    assert body["citations"], "draft without citations is ungrounded"
    matter_refs = [c for c in body["citations"] if c["domain"] == "matter"]
    assert matter_refs
    for ref in matter_refs:
        assert {"document_id", "page", "span"} <= set(ref)


def test_draft_cites_only_stored_authority_text(
    client: TestClient, db_session_factory
) -> None:
    """Authority citations in a draft come from stored matter context only."""
    matter_id = _make_analysed_matter(client, db_session_factory)
    body = _create_draft(client, matter_id, draft_type="opinion")
    auth_refs = [c for c in body["citations"] if c["domain"] == "authority"]
    assert auth_refs, "stored authority citation must surface in the draft"
    assert all(c["article_or_section"] == "Art. 999 (synthetic)" for c in auth_refs)
    assert "Art. 999 (synthetic)" in body["content"]
    assert "Art. 237" not in body["content"], "fabricated article text forbidden"


def test_transition_draft_to_acknowledged(
    client: TestClient, db_session_factory
) -> None:
    """draft → acknowledged works with the acknowledged label."""
    matter_id = _make_analysed_matter(client, db_session_factory)
    created = _create_draft(client, matter_id, draft_type="client_email")
    resp = client.post(f"/api/v1/drafts/{created['draft_id']}/acknowledge")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review_state"] == "acknowledged"
    assert body["reviewer"] is None
    assert "acknowledged" in body["status_label"].lower()


def test_transition_draft_to_lawyer_reviewed_records_reviewer(
    client: TestClient, db_session_factory
) -> None:
    """draft → lawyer_reviewed records reviewer + timestamp explicitly."""
    matter_id = _make_analysed_matter(client, db_session_factory)
    created = _create_draft(client, matter_id)
    resp = client.post(
        f"/api/v1/drafts/{created['draft_id']}/lawyer_review",
        json={"reviewer": "Me. Salma Idrissi"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["review_state"] == "lawyer_reviewed"
    assert body["reviewer"] == "Me. Salma Idrissi"
    assert body["reviewed_at"], "lawyer review must carry a timestamp"
    assert "Me. Salma Idrissi" in body["status_label"]


def test_unknown_transition_returns_400(client: TestClient, db_session_factory) -> None:
    """Transition to an unknown review state is rejected with 400."""
    matter_id = _make_analysed_matter(client, db_session_factory)
    created = _create_draft(client, matter_id)
    resp = client.post(
        f"/api/v1/drafts/{created['draft_id']}/transition",
        json={"to_state": "partner_approved"},
    )
    assert resp.status_code == 400, resp.text


def test_unknown_draft_type_returns_400(client: TestClient, db_session_factory) -> None:
    """Unknown draft types are rejected with 400, never generated."""
    matter_id = _make_analysed_matter(client, db_session_factory)
    resp = client.post(
        f"/api/v1/matters/{matter_id}/drafts", json={"draft_type": "verdict"}
    )
    assert resp.status_code == 400, resp.text


def test_draft_without_analysis_returns_422(
    client: TestClient, db_session_factory
) -> None:
    """Drafts require a prior analysis; nothing is fabricated from thin air."""
    matter_id = _make_empty_matter(db_session_factory)
    resp = client.post(
        f"/api/v1/matters/{matter_id}/drafts", json={"draft_type": "memo"}
    )
    assert resp.status_code == 422, resp.text


def test_polish_blocked_strict_external_with_matter_evidence(
    client: TestClient, db_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional LLM polish routes through check_privacy BEFORE any provider call."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("MATTER_PRIVACY_MODE", "strict")
    matter_id = _make_analysed_matter(client, db_session_factory)
    resp = client.post(
        f"/api/v1/matters/{matter_id}/drafts",
        json={"draft_type": "memo", "polish": True},
    )
    assert resp.status_code == 403, resp.text
