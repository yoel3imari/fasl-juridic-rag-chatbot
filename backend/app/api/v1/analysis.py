"""Analysis route: POST /api/v1/matters/{matter_id}/analysis.

Builds facts/issues/gaps/risks + plain-language summaries from matter
evidence (DocumentSection rows) + prior chat (user-role Message rows),
persists the result in an Analysis row (kind, content_json), and returns it.
Analysis content lives ONLY in Analysis rows — document rows are read-only
here. Rule-based only: no provider call, no privacy implications.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.engine import build_analysis
from app.models.analysis import Analysis
from app.models.base import get_db
from app.models.conversation import Conversation, Message
from app.models.document import Document
from app.models.document_section import DocumentSection
from app.models.matter import Matter

router = APIRouter(prefix="/api/v1/matters", tags=["analysis"])

ANALYSIS_KIND = "facts-issues-gaps-risks"

Risk = Literal["High", "Medium", "Low"]


class AnalysisIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str = ANALYSIS_KIND


class AnalysisOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    analysis_id: int
    kind: str
    content: dict[str, Any]


@router.post("/{matter_id}/analysis", response_model=AnalysisOut)
async def create_analysis(
    matter_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
    body: AnalysisIn | None = None,
) -> AnalysisOut:
    """Run the rule-based analysis over stored matter evidence and persist it."""
    try:
        matter = await session.get(Matter, matter_id)
        if matter is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="matter not found"
            )
        sections = (
            (
                await session.execute(
                    select(DocumentSection)
                    .where(DocumentSection.matter_id == matter_id)
                    .order_by(DocumentSection.id)
                )
            )
            .scalars()
            .all()
        )
        doc_types = list(
            (
                await session.execute(
                    select(Document.doc_type).where(Document.matter_id == matter_id)
                )
            ).scalars()
        )
        user_messages = list(
            (
                await session.execute(
                    select(Message.content)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(
                        Conversation.matter_id == matter_id,
                        Message.role == "user",
                    )
                    .order_by(Message.id)
                )
            ).scalars()
        )
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
        content = build_analysis(
            section_dicts, doc_types, [str(m) for m in user_messages]
        )
        row = Analysis(
            matter_id=matter_id,
            kind=(body.kind if body else ANALYSIS_KIND),
            content_json=content,
        )
        session.add(row)
        await session.flush()
        analysis_id = row.id
        await session.commit()
    except HTTPException:
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"analysis failed: {exc}",
        ) from exc
    return AnalysisOut(
        matter_id=matter_id, analysis_id=analysis_id, kind=row.kind, content=content
    )
