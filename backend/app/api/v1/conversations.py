"""Conversation history routes: list conversations and get conversation details.

Query construction lives in `app.repositories.conversation`; this module only
shapes rows into the response models and maps missing rows onto 404.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, map_error
from app.models.base import get_db
from app.repositories.conversation import ConversationRepository

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
    repo = ConversationRepository(session)
    rows = await repo.list_recent(matter_id=matter_id, limit=limit)
    return [
        ConversationOut(
            id=r.id,
            matter_id=r.matter_id,
            matter_title=r.matter_title or f"Matter #{r.matter_id}",
            title=r.title,
            created_at=r.created_at,
            message_count=r.message_count,
            preview=r.preview,
        )
        for r in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ConversationDetailOut:
    """Get conversation details along with all messages in chronological order."""
    repo = ConversationRepository(session)
    conv = await repo.get(conversation_id)
    if conv is None:
        raise HTTPException(*map_error(NotFoundError("conversation not found")))

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
    repo = ConversationRepository(session)
    deleted = await repo.delete(conversation_id)
    if not deleted:
        raise HTTPException(*map_error(NotFoundError("conversation not found")))
    await session.commit()
    return {"status": "deleted", "id": conversation_id}
