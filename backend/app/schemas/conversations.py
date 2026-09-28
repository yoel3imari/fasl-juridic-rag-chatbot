"""Conversation-history schemas (todo 41 extraction from app.api.v1.conversations)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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
