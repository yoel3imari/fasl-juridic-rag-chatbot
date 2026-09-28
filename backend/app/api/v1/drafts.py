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
through app.domain.privacy.check_privacy BEFORE any provider call (strict + external
+ matter evidence → 403).

Prefix note: this module exposes two routers because one APIRouter prefix
cannot serve both families: `matters_router` (prefix /api/v1/matters) hosts
POST /{matter_id}/drafts, while `drafts_router` (prefix /api/v1/drafts)
hosts the three /{draft_id}/... transitions. Served paths are unchanged
from the previous single unprefixed router.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.draft_schemas import (
    DraftCreateIn,
    DraftOut,
    LawyerReviewIn,
    TransitionIn,
)
from app.config import Settings
from app.config.resolver import (
    ResolvedLlmSettings,
    resolve_privacy_mode,
    resolve_request_llm_settings,
)
from app.domain import drafts as drafts_mod
from app.domain.privacy import (
    ConsentRequiredError,
    PrivacyViolationError,
    check_privacy,
)
from app.infrastructure import llm as llm_mod
from app.models.base import get_db
from app.models.draft import Draft, ReviewState
from app.repositories.draft import DraftRepository

matters_router = APIRouter(prefix="/api/v1/matters", tags=["drafts"])
drafts_router = APIRouter(prefix="/api/v1/drafts", tags=["drafts"])


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


async def _polish_text(text: str, resolved: ResolvedLlmSettings) -> tuple[str, bool]:
    """Best-effort LLM polish; any provider failure keeps the template."""
    try:
        agent = llm_mod.get_agent(
            provider=resolved.provider, model=resolved.model
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


@matters_router.post(
    "/{matter_id}/drafts",
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
    resolved = resolve_request_llm_settings(settings)
    try:
        if body.draft_type not in drafts_mod.DRAFT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown draft_type: {body.draft_type!r}",
            )
        repo = DraftRepository(session)
        if not await repo.matter_exists(matter_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="matter not found"
            )
        analysis = await repo.latest_analysis(matter_id)
        if analysis is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="no analysis for this matter; POST analysis first",
            )
        stored = await repo.stored_authority(matter_id)
        text, citations = drafts_mod.build_draft(
            body.draft_type,
            dict(analysis.content_json),
            stored_authority=stored,
        )
        # Release the read transaction before the optional LLM polish call so
        # no pooled connection is held across network I/O. Nothing is pending
        # here (the Draft row is added after), so this only ends the read txn.
        await session.commit()
        polished = False
        if body.polish:
            try:
                check_privacy(
                    text,
                    provider=resolved.provider,
                    privacy_mode=resolve_privacy_mode(settings),
                    consent=body.consent,
                    has_matter_evidence=bool(
                        [c for c in citations if c.get("domain") == "matter"]
                    ),
                )
            except PrivacyViolationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
                ) from exc
            except ConsentRequiredError as exc:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
                ) from exc
            text, polished = await _polish_text(text, resolved)
        row = await repo.create(matter_id, body.draft_type, text)
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
        repo = DraftRepository(session)
        row = await repo.get(draft_id)
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
        analysis = await repo.latest_analysis(row.matter_id)
        content_json = dict(analysis.content_json) if analysis is not None else {}
        stored = await repo.stored_authority(row.matter_id)
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


@drafts_router.post("/{draft_id}/acknowledge", response_model=DraftOut)
async def acknowledge_draft(
    draft_id: int, session: Annotated[AsyncSession, Depends(get_db)]
) -> DraftOut:
    """Regular-user acknowledgement; never renders "lawyer review"."""
    return await _apply_transition(
        session, draft_id, TransitionIn(to_state="acknowledged")
    )


@drafts_router.post("/{draft_id}/lawyer_review", response_model=DraftOut)
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


@drafts_router.post("/{draft_id}/transition", response_model=DraftOut)
async def transition_draft(
    draft_id: int,
    body: TransitionIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> DraftOut:
    """Generic transition; unknown to_state → 400."""
    return await _apply_transition(session, draft_id, body)
