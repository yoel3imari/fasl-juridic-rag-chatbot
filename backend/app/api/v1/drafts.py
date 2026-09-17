"""Drafting routes (task 9): create grounded drafts + explicit review states.

Endpoints:
- POST /api/v1/matters/{matter_id}/drafts → 201 with the draft payload.
- POST /api/v1/drafts/{draft_id}/acknowledge → review_state=acknowledged.
- POST /api/v1/drafts/{draft_id}/lawyer_review {reviewer} → lawyer_reviewed.
- POST /api/v1/drafts/{draft_id}/transition {to_state, reviewer?} → generic
  transition; unknown to_state → 400.

Response shape: see app.api.v1.draft_schemas (contract for task 10).

Grounding: drafts assemble from the matter's latest Analysis content_json
plus authority citations already stored in the matter's message
citations_json. Unknown draft_type → 400; matter without analysis → 422
(nothing is fabricated); unknown to_state → 400; leaving lawyer_reviewed
→ 409. The template path makes zero provider calls; polish=True routes
through app.llm.check_privacy BEFORE any provider call (strict + external
+ matter evidence → 403).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import drafts as drafts_mod
from app import llm as llm_mod
from app.api.v1.draft_schemas import (
    DraftCreateIn,
    DraftOut,
    LawyerReviewIn,
    TransitionIn,
)
from app.config import Settings
from app.models.analysis import Analysis
from app.models.base import get_db
from app.models.conversation import Conversation, Message
from app.models.draft import Draft, ReviewState
from app.models.matter import Matter

router = APIRouter(tags=["drafts"])


def _to_out(
    row: Draft, content: str, citations: list[dict[str, Any]], polished: bool
) -> DraftOut:
    state = (
        row.review_state.value
        if isinstance(row.review_state, ReviewState)
        else str(row.review_state)
    )
    return DraftOut(
        matter_id=row.matter_id,
        draft_id=row.id,
        draft_type=row.draft_type,
        content=content,
        review_state=state,
        provisional_banner=drafts_mod.PROVISIONAL_BANNER,
        provisional_banner_ar=drafts_mod.PROVISIONAL_BANNER_AR,
        provisional_banner_fr=drafts_mod.PROVISIONAL_BANNER_FR,
        status_label=drafts_mod.status_label(state, row.reviewer),
        citations=citations,
        reviewer=row.reviewer,
        reviewed_at=row.reviewed_at,
        polished=polished,
    )


async def _latest_analysis(session: AsyncSession, matter_id: int) -> Analysis | None:
    return (
        (
            await session.execute(
                select(Analysis)
                .where(Analysis.matter_id == matter_id)
                .order_by(Analysis.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _stored_authority(
    session: AsyncSession, matter_id: int
) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                select(Message.citations_json)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(
                    Conversation.matter_id == matter_id,
                    Message.citations_json.is_not(None),
                )
                .order_by(Message.id)
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for cell in rows:
        if isinstance(cell, list):
            out.extend(c for c in cell if isinstance(c, dict))
    return out


async def _polish_text(text: str, settings: Settings) -> tuple[str, bool]:
    """Best-effort LLM polish; any provider failure keeps the template."""
    try:
        agent = llm_mod.get_agent(
            provider=settings.LLM_PROVIDER, model=settings.LLM_MODEL
        )
        result = await agent.run(
            "Polish the provisional draft below for clarity without adding, "
            "removing, or altering any fact, citation, or number. Keep every "
            "[matter: ...] and [authority: ...] reference exactly as written "
            "and keep the opening banner line unchanged.\n\n" + text
        )
        polished = str(result.output).strip()
        return (polished or text), bool(polished)
    except Exception:
        return text, False


@router.post(
    "/api/v1/matters/{matter_id}/drafts",
    response_model=DraftOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft(
    matter_id: int,
    body: DraftCreateIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DraftOut:
    """Assemble a grounded draft from the matter's latest analysis."""
    settings = Settings()  # type: ignore[call-arg]
    try:
        if body.draft_type not in drafts_mod.DRAFT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown draft_type: {body.draft_type!r}",
            )
        matter = await session.get(Matter, matter_id)
        if matter is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="matter not found"
            )
        analysis = await _latest_analysis(session, matter_id)
        if analysis is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="no analysis for this matter; POST analysis first",
            )
        stored = await _stored_authority(session, matter_id)
        text, citations = drafts_mod.build_draft(
            body.draft_type,
            dict(analysis.content_json),
            stored_authority=stored,
        )
        polished = False
        if body.polish:
            try:
                llm_mod.check_privacy(
                    text,
                    provider=settings.LLM_PROVIDER,
                    privacy_mode=settings.MATTER_PRIVACY_MODE,
                    consent=body.consent,
                    has_matter_evidence=bool(
                        [c for c in citations if c.get("domain") == "matter"]
                    ),
                )
            except llm_mod.PrivacyViolationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
                ) from exc
            except llm_mod.ConsentRequiredError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
                ) from exc
            text, polished = await _polish_text(text, settings)
        row = Draft(
            matter_id=matter_id,
            draft_type=body.draft_type,
            content=text,
            review_state=ReviewState.DRAFT,
        )
        session.add(row)
        await session.flush()
        await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"draft creation failed: {exc}",
        ) from exc
    return _to_out(row, text, citations, polished)


async def _apply_transition(
    session: AsyncSession, draft_id: int, transition: TransitionIn
) -> DraftOut:
    """Shared transition core: validate → persist → render (citations rebuilt)."""
    to_state, reviewer = transition.to_state, transition.reviewer
    try:
        row = await session.get(Draft, draft_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="draft not found"
            )
        current = (
            row.review_state.value
            if isinstance(row.review_state, ReviewState)
            else str(row.review_state)
        )
        try:
            new_state = drafts_mod.resolve_transition(current, to_state, reviewer)
        except drafts_mod.IllegalTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        row.review_state = ReviewState(new_state)
        if new_state == ReviewState.LAWYER_REVIEWED.value:
            row.reviewer = (reviewer or "").strip()
            row.reviewed_at = datetime.now(timezone.utc)
        analysis = await _latest_analysis(session, row.matter_id)
        content_json = dict(analysis.content_json) if analysis is not None else {}
        stored = await _stored_authority(session, row.matter_id)
        _text, citations = drafts_mod.build_draft(
            row.draft_type, content_json, stored_authority=stored
        )
        await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"transition failed: {exc}",
        ) from exc
    return _to_out(row, row.content, citations, False)


@router.post("/api/v1/drafts/{draft_id}/acknowledge", response_model=DraftOut)
async def acknowledge_draft(
    draft_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> DraftOut:
    """Regular-user acknowledgement; never renders "lawyer review"."""
    return await _apply_transition(
        session, draft_id, TransitionIn(to_state="acknowledged")
    )


@router.post("/api/v1/drafts/{draft_id}/lawyer_review", response_model=DraftOut)
async def lawyer_review_draft(
    draft_id: int,
    body: LawyerReviewIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DraftOut:
    """Counsel review; records reviewer + timestamp explicitly."""
    return await _apply_transition(
        session,
        draft_id,
        TransitionIn(to_state="lawyer_reviewed", reviewer=body.reviewer),
    )


@router.post("/api/v1/drafts/{draft_id}/transition", response_model=DraftOut)
async def transition_draft(
    draft_id: int,
    body: TransitionIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DraftOut:
    """Generic transition; unknown to_state → 400."""
    return await _apply_transition(session, draft_id, body)
