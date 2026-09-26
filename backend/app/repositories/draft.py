"""Draft repository: the reads and writes the draft endpoints need.

Draft assembly needs the matter's latest analysis and the authority citations
already stored on chat messages, then persists the Draft row. That query
construction lives here. The review-state machine stays out: `resolve_transition`
is pure domain logic in `app.domain.drafts`, and the router drives it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import Analysis
from app.models.conversation import Conversation, Message
from app.models.draft import Draft, ReviewState
from app.models.matter import Matter


class DraftRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def matter_exists(self, matter_id: int) -> bool:
        return await self.session.get(Matter, matter_id) is not None

    async def latest_analysis(self, matter_id: int) -> Analysis | None:
        """Newest Analysis row for the matter, or None."""
        return (
            (
                await self.session.execute(
                    select(Analysis)
                    .where(Analysis.matter_id == matter_id)
                    .order_by(Analysis.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    async def stored_authority(self, matter_id: int) -> list[dict[str, Any]]:
        """Authority citations already stored on the matter's chat messages."""
        rows = (
            (
                await self.session.execute(
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

    async def get(self, draft_id: int) -> Draft | None:
        return await self.session.get(Draft, draft_id)

    async def create(self, matter_id: int, draft_type: str, content: str) -> Draft:
        """Insert a draft in the DRAFT review state and return it. Caller commits."""
        row = Draft(
            matter_id=matter_id,
            draft_type=draft_type,
            content=content,
            review_state=ReviewState.DRAFT,
        )
        self.session.add(row)
        await self.session.flush()
        return row
