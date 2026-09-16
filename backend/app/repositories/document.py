"""Document repository: CRUD always scoped to a single matter.

Every read/write takes ``matter_id`` so documents can never cross
matter boundaries.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        matter_id: int,
        filename: str,
        original_name: str,
        mime_type: str,
        doc_type: str,
        status: str = "uploaded",
        chunk_count: int = 0,
    ) -> Document:
        doc = Document(
            matter_id=matter_id,
            filename=filename,
            original_name=original_name,
            mime_type=mime_type,
            doc_type=doc_type,
            status=status,
            chunk_count=chunk_count,
        )
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def get(self, document_id: int, matter_id: int) -> Document | None:
        """Fetch a document only if it belongs to ``matter_id``."""
        result = await self.session.execute(
            select(Document).where(
                Document.id == document_id, Document.matter_id == matter_id
            )
        )
        return result.scalars().first()

    async def list_for_matter(self, matter_id: int) -> list[Document]:
        """List documents visible inside one matter — never across matters."""
        result = await self.session.execute(
            select(Document)
            .where(Document.matter_id == matter_id)
            .order_by(Document.id)
        )
        return list(result.scalars().all())

    async def update_status(
        self, document_id: int, matter_id: int, status: str
    ) -> Document | None:
        doc = await self.get(document_id, matter_id)
        if doc is None:
            return None
        doc.status = status
        await self.session.flush()
        return doc

    async def update_chunk_count(
        self, document_id: int, matter_id: int, chunk_count: int
    ) -> Document | None:
        doc = await self.get(document_id, matter_id)
        if doc is None:
            return None
        doc.chunk_count = chunk_count
        await self.session.flush()
        return doc

    async def delete(self, document_id: int, matter_id: int) -> bool:
        doc = await self.get(document_id, matter_id)
        if doc is None:
            return False
        await self.session.delete(doc)
        await self.session.flush()
        return True
