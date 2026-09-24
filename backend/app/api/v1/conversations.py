"""Conversation history routes: list conversations and get conversation details."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.base import get_db
from app.models.conversation import Conversation, Message
from app.models.matter import Matter

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


class MessageOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    conversation_id: int
    role: str
    content: str
    citations_json: Any | None = None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    matter_id: int
    matter_title: str | None = None
    title: str
    created_at: datetime
    message_count: int = 0
    preview: str | None = None


class ConversationDetailOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    matter_id: int
    matter_title: str | None = None
    title: str
    created_at: datetime
    messages: list[MessageOut]


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    session: Annotated[AsyncSession, Depends(get_db)],
    matter_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ConversationOut]:
    """List recent conversations with matter info, message count, and preview."""
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

    rows = (await session.execute(stmt)).all()

    conv_ids = [r.id for r in rows]
    previews: dict[int, str] = {}
    if conv_ids:
        msg_stmt = (
            select(Message.conversation_id, Message.content)
            .where(Message.conversation_id.in_(conv_ids))
            .order_by(Message.id.asc())
        )
        msg_rows = (await session.execute(msg_stmt)).all()
        for c_id, content in msg_rows:
            if c_id not in previews and content:
                previews[c_id] = content[:120]

    return [
        ConversationOut(
            id=r.id,
            matter_id=r.matter_id,
            matter_title=r.matter_title or f"Matter #{r.matter_id}",
            title=r.title,
            created_at=r.created_at,
            message_count=r.message_count,
            preview=previews.get(r.id),
        )
        for r in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationDetailOut:
    """Get conversation details along with all messages in chronological order."""
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.matter), selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    res = await session.execute(stmt)
    conv = res.scalars().first()
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation not found",
        )

    sorted_messages = sorted(conv.messages, key=lambda m: m.id)

    return ConversationDetailOut(
        id=conv.id,
        matter_id=conv.matter_id,
        matter_title=conv.matter.title if conv.matter else f"Matter #{conv.matter_id}",
        title=conv.title,
        created_at=conv.created_at,
        messages=[
            MessageOut(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,
                content=m.content,
                citations_json=m.citations_json,
                created_at=m.created_at,
            )
            for m in sorted_messages
        ],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_200_OK)
async def delete_conversation(
    conversation_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Delete a conversation and all its messages."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="conversation not found",
        )
    await session.delete(conv)
    await session.commit()
    return {"status": "deleted", "id": conversation_id}
