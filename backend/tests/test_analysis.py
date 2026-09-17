"""TDD (red-first): facts/issues/gaps/risks + missing-info + plain-language analysis (task 8).

Given: POST /api/v1/matters/{matter_id}/analysis builds from matter evidence
       (DocumentSection rows via DB) + prior chat messages.
When: the endpoint runs rule-based extraction over stored normalized_text.
Then: parties/dates/obligations carry source spans; issues carry risk +
      span refs; gaps enumerate missing doc types; zero documents yields an
      explicit needs-documents response (never fabricated facts); glossary
      terms render plain-language explanations; source statements, client
      assertions, verified facts, and AI inferences stay in SEPARATE kinds
      with contradictions retained side by side.

All fixtures below are SYNTHETIC test data — never real legal text.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import app
from app.models import Base, Conversation, Document, DocumentSection, Matter, Message
from app.models.base import get_db

LETTER_TEXT = (
    "Termination letter. Mr. Karim Bennani, your employment contract is terminated "
    "effective 2024-03-15. Your notice period is 1 month. Final compensation "
    "of 15000 MAD will be paid. Signed by Societe Atlas SARL."
)


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


def _make_matter_with_letter(db_session_factory) -> int:
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


def _make_matter_with_contradiction(db_session_factory) -> int:
    import anyio

    async def _seed() -> int:
        async with db_session_factory() as sess:
            matter = Matter(
                title="Contradiction matter",
                matter_type="labor",
                jurisdiction="casablanca",
                language="ar",
            )
            sess.add(matter)
            await sess.flush()
            doc = Document(
                matter_id=matter.id,
                filename="clauses.txt",
                original_name="Clauses.txt",
                mime_type="text/plain",
                doc_type="contract",
                status="indexed",
                chunk_count=2,
            )
            sess.add(doc)
            await sess.flush()
            sess.add(
                _section(
                    doc.id,
                    matter.id,
                    "Article 4: the notice period is 1 month from 2024-03-15.",
                    title="Article 4",
                    section_id="art-4",
                    page=1,
                )
            )
            sess.add(
                _section(
                    doc.id,
                    matter.id,
                    "Article 9: the notice period is 3 months from 2024-03-15.",
                    title="Article 9",
                    section_id="art-9",
                    page=2,
                )
            )
            conv = Conversation(matter_id=matter.id, title="chat")
            sess.add(conv)
            await sess.flush()
            sess.add(
                Message(
                    conversation_id=conv.id,
                    role="user",
                    content="My employer owes me three months salary.",
                )
            )
            await sess.commit()
            return matter.id

    return anyio.run(_seed)


def test_gaps_list_requests_payslips(client: TestClient, db_session_factory) -> None:
    """Termination letter alone → gaps mention payslips/correspondence/contract."""
    from app.analysis import engine as eng_mod

    assert eng_mod  # analysis engine module exists
    matter_id = _make_matter_with_letter(db_session_factory)
    resp = client.post(f"/api/v1/matters/{matter_id}/analysis", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    gaps = body["content"]["gaps"]
    joined = " ".join(str(g) for g in gaps).lower()
    assert "payslip" in joined
    assert "correspondence" in joined
    assert "contract" in joined


def test_risks_table_span_refs(client: TestClient, db_session_factory) -> None:
    """Issues table rows each carry risk level + span refs to evidence."""
    matter_id = _make_matter_with_letter(db_session_factory)
    resp = client.post(f"/api/v1/matters/{matter_id}/analysis", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    issues = body["content"]["issues"]
    assert len(issues) >= 3
    for row in issues:
        assert row["risk"] in ("High", "Medium", "Low")
        assert row["span_refs"], f"issue without span refs: {row}"
        for ref in row["span_refs"]:
            assert {"document_id", "page", "span"} <= set(ref)


def test_contradictory_clauses_both_retained(
    client: TestClient, db_session_factory
) -> None:
    """Contradictory spans retained side by side; knowledge kinds separated."""
    matter_id = _make_matter_with_contradiction(db_session_factory)
    resp = client.post(f"/api/v1/matters/{matter_id}/analysis", json={})
    assert resp.status_code == 200, resp.text
    content = resp.json()["content"]
    contradictions = content["contradictions"]
    assert len(contradictions) >= 1
    pair = contradictions[0]
    assert pair["a"] != pair["b"]
    knowledge = content["knowledge"]
    assert set(knowledge) == {
        "source_statements",
        "client_assertions",
        "verified_facts",
        "ai_inferences",
    }
    # Client chat assertion kept as assertion, never collapsed into fact.
    assertions = " ".join(str(x) for x in knowledge["client_assertions"]).lower()
    assert "three months salary" in assertions
    facts = " ".join(str(x) for x in knowledge["verified_facts"]).lower()
    assert "three months salary" not in facts
    # Both contradictory source spans still present.
    sources = " ".join(str(x) for x in knowledge["source_statements"])
    assert "1 month" in sources and "3 months" in sources


def test_zero_documents_needs_documents(client: TestClient, db_session_factory) -> None:
    """Matter with zero documents → explicit needs-documents, no fabricated facts."""
    matter_id = _make_empty_matter(db_session_factory)
    resp = client.post(f"/api/v1/matters/{matter_id}/analysis", json={})
    assert resp.status_code == 200, resp.text
    content = resp.json()["content"]
    assert content["status"] == "needs-documents"
    assert content["parties"] == []
    assert content["dates"] == []
    assert content["obligations"] == []
    assert content["issues"] == []
    assert any("document" in str(g).lower() for g in content["gaps"])


def test_glossary_plain_language(client: TestClient, db_session_factory) -> None:
    """Glossary terms render plain-language Arabic + French explanations."""
    matter_id = _make_matter_with_letter(db_session_factory)
    resp = client.post(f"/api/v1/matters/{matter_id}/analysis", json={})
    assert resp.status_code == 200, resp.text
    content = resp.json()["content"]
    assert content["summary_ar"].strip()
    assert content["summary_fr"].strip()
    # 'compensation' appears in the letter; its plain explanations must render.
    assert "plain_ar" in content["glossary_used"][0]
    used = {g["term"] for g in content["glossary_used"]}
    assert "compensation" in used
