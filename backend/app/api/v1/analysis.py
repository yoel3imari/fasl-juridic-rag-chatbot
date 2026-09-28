"""Analysis route: POST /api/v1/matters/{matter_id}/analysis.

Builds facts/issues/gaps/risks + plain-language summaries from matter
evidence (DocumentSection rows) + prior chat (user-role Message rows),
persists the result in an Analysis row (kind, content_json), and returns it.
Analysis content lives ONLY in Analysis rows — document rows are read-only
here. Rule-based only: no provider call, no privacy implications.

Query construction lives in `app.repositories.analysis`; this module
sequences the calls, runs the pure rule, and owns the transaction.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, map_error
from app.domain.analysis.engine import build_analysis
from app.models.base import get_db
from app.repositories.analysis import AnalysisRepository
from app.schemas.analysis import ANALYSIS_KIND, AnalysisIn, AnalysisOut

router = APIRouter(prefix="/api/v1/matters", tags=["analysis"])

Risk = Literal["High", "Medium", "Low"]


@router.post("/{matter_id}/analysis", response_model=AnalysisOut)
async def create_analysis(
    matter_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    body: AnalysisIn | None = None,
) -> AnalysisOut:
    """Run the rule-based analysis over stored matter evidence and persist it."""
    try:
        repo = AnalysisRepository(session)
        if not await repo.matter_exists(matter_id):
            raise HTTPException(*map_error(NotFoundError("matter not found")))
        sections = await repo.list_sections(matter_id)
        doc_types = await repo.list_doc_types(matter_id)
        user_messages = await repo.list_user_message_contents(matter_id)
        section_dicts = [
            {
                "document_id": s.document_id,
                "page": s.page_start,
                "span": [s.span_start, s.span_end],
                "title": s.title,
                "text": s.normalized_text,
                "needs_review": s.needs_review,
            }
            for s in sections
        ]
        content = build_analysis(section_dicts, doc_types, user_messages)
        row = await repo.create(matter_id, body.kind if body else ANALYSIS_KIND, content)
        analysis_id = row.id
        await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(*map_error(exc, context="analysis failed")) from exc
    return AnalysisOut(matter_id=matter_id, analysis_id=analysis_id, kind=row.kind, content=content)
