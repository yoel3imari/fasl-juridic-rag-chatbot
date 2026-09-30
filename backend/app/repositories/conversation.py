"""Conversation repository: reads and deletes for conversations and messages.

The list query is the interesting one: it needs a LEFT JOIN to matters for the
title, a LEFT JOIN to messages for the count, a GROUP BY over the non-aggregated
columns, and a second query for the first-message preview. All of that belongs
here so the router only shapes rows into its response models.

`ConversationSummary` is a plain record, not an HTTP type: the router maps it
onto `ConversationOut`.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.conversation import Conversation, Message
from app.models.matter import Matter

PREVIEW_CHARS = 120


class ConversationSummary(NamedTuple):
    """One row of the conversation list, with its preview already resolved."""

    id: int
    matter_id: int | None
    title: str
    created_at: datetime
    matter_title: str | None
    message_count: int
    preview: str | None


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_recent(
        self, matter_id: int | None = None, limit: int = 50
    ) -> list[ConversationSummary]:
        """Recent conversations with matter title, message count and preview."""
        stmt = (
            select(
                Conversation.id,
                Conversation.matter_id,
                Conversation.title,
                Conversation.created_at,
                Matter.title.label("matter_title"),
                func.count(Message.id).label("message_count"),
            )
            .outerjoin(Matter, Conversation.matter_id == Matter.id)
            .outerjoin(Message, Conversation.id == Message.conversation_id)
        )
        if matter_id is not None:
            stmt = stmt.where(Conversation.matter_id == matter_id)

        stmt = (
            stmt.group_by(
                Conversation.id,
                Conversation.matter_id,
                Conversation.title,
                Conversation.created_at,
                Matter.title,
            )
            .order_by(desc(Conversation.id))
            .limit(limit)
        )

        rows = (await self.session.execute(stmt)).all()

        conv_ids = [r.id for r in rows]
        previews: dict[int, str] = {}
        if conv_ids:
            msg_stmt = (
                select(Message.conversation_id, Message.content)
                .where(Message.conversation_id.in_(conv_ids))
                .order_by(Message.id.asc())
            )
            msg_rows = (await self.session.execute(msg_stmt)).all()
            for c_id, content in msg_rows:
                if c_id not in previews and content:
                    previews[c_id] = content[:PREVIEW_CHARS]

        return [
            ConversationSummary(
                id=r.id,
                matter_id=r.matter_id,
                title=r.title,
                created_at=r.created_at,
                matter_title=r.matter_title,
                message_count=r.message_count,
                preview=previews.get(r.id),
            )
            for r in rows
        ]

    async def get(self, conversation_id: int) -> Conversation | None:
        """One conversation with its matter and messages eagerly loaded."""
        stmt = (
            select(Conversation)
            .options(selectinload(Conversation.matter), selectinload(Conversation.messages))
            .where(Conversation.id == conversation_id)
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def delete(self, conversation_id: int) -> bool:
        """Delete a conversation and its messages; False when it does not exist.

        The caller owns the transaction boundary and commits.
        """
        conv = await self.session.get(Conversation, conversation_id)
        if conv is None:
            return False
        await self.session.delete(conv)
        await self.session.flush()
        return True
