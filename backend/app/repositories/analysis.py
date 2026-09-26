"""Analysis repository: matter lookup, evidence fetches, and Analysis persistence.

`create_analysis` needs three different reads (sections, document types, and the
user's prior messages) before it can run the pure rule in
`app.domain.analysis.engine`. All of that query construction lives here so the
router only sequences the calls and maps the result onto its response model.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import Analysis
from app.models.conversation import Conversation, Message
from app.models.document import Document
from app.models.document_section import DocumentSection
from app.models.matter import Matter


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def matter_exists(self, matter_id: int) -> bool:
        return await self.session.get(Matter, matter_id) is not None

    async def list_sections(self, matter_id: int) -> list[DocumentSection]:
        """Every document section in the matter, oldest first."""
        result = await self.session.execute(
            select(DocumentSection)
            .where(DocumentSection.matter_id == matter_id)
            .order_by(DocumentSection.id)
        )
        return list(result.scalars().all())

    async def list_doc_types(self, matter_id: int) -> list[str]:
        """The `doc_type` of every document in the matter."""
        result = await self.session.execute(
            select(Document.doc_type).where(Document.matter_id == matter_id)
        )
        return list(result.scalars())

    async def list_user_message_contents(self, matter_id: int) -> list[str]:
        """User-role message text across the matter's conversations, oldest first."""
        result = await self.session.execute(
            select(Message.content)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.matter_id == matter_id, Message.role == "user")
            .order_by(Message.id)
        )
        return [str(m) for m in result.scalars()]

    async def create(self, matter_id: int, kind: str, content: dict[str, Any]) -> Analysis:
        """Persist an Analysis row and return it. The caller commits."""
        row = Analysis(matter_id=matter_id, kind=kind, content_json=content)
        self.session.add(row)
        await self.session.flush()
        return row
