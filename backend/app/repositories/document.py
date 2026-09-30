"""Document repository: CRUD always scoped to a single matter.

Every read/write takes ``matter_id`` so documents can never cross
matter boundaries.
"""

from typing import NamedTuple

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentVersion
from app.models.document_section import DocumentSection


class DocumentListRow(NamedTuple):
    """One document plus its section/page aggregates (from an outer join)."""

    document: Document
    section_count: int
    page_count: int | None


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
            select(Document).where(Document.id == document_id, Document.matter_id == matter_id)
        )
        return result.scalars().first()

    async def list_for_matter(self, matter_id: int) -> list[Document]:
        """List documents visible inside one matter — never across matters."""
        result = await self.session.execute(
            select(Document).where(Document.matter_id == matter_id).order_by(Document.id)
        )
        return list(result.scalars().all())

    async def list_detail_for_matter(self, matter_id: int) -> list[DocumentListRow]:
        """Documents of one matter with section count and max page_end.

        Outer join: a document with no sections yields ``section_count == 0``
        and ``page_count is None``. Ordered by document id ascending.
        """
        stmt = (
            select(
                Document,
                func.count(DocumentSection.id).label("section_count"),
                func.max(DocumentSection.page_end).label("page_count"),
            )
            .outerjoin(DocumentSection, DocumentSection.document_id == Document.id)
            .where(Document.matter_id == matter_id)
            .group_by(Document.id)
            .order_by(Document.id)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            DocumentListRow(
                document=row[0],
                section_count=int(row[1]),
                page_count=None if row[2] is None else int(row[2]),
            )
            for row in rows
        ]

    async def delete(self, document_id: int, matter_id: int) -> bool:
        doc = await self.get(document_id, matter_id)
        if doc is None:
            return False
        await self.session.delete(doc)
        await self.session.flush()
        return True

    async def blob_refs(self, document_id: int, matter_id: int) -> list[str]:
        """Original-file refs of one document, scoped to its matter."""
        stmt = (
            select(DocumentVersion.blob_ref)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.document_id == document_id,
                Document.matter_id == matter_id,
            )
            .order_by(DocumentVersion.id)
        )
        return [str(ref) for ref in (await self.session.execute(stmt)).scalars()]

    async def blob_refs_for_matter(self, matter_id: int) -> list[str]:
        """Original-file refs of every document in one matter."""
        stmt = (
            select(DocumentVersion.blob_ref)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.matter_id == matter_id)
            .order_by(DocumentVersion.id)
        )
        return [str(ref) for ref in (await self.session.execute(stmt)).scalars()]

    async def count_for_matter(self, matter_id: int) -> int:
        """Document count for one matter."""
        result = await self.session.execute(
            select(func.count()).select_from(Document).where(Document.matter_id == matter_id)
        )
        return int(result.scalar_one())

    async def delete_sections(self, document_id: int, matter_id: int) -> int:
        """Bulk-delete one document's section rows; rows gone, 0 when out of scope.

        Sections have no ORM relationship on Document and aiosqlite does not
        enable ``PRAGMA foreign_keys=ON``, so hard deletes must issue this
        explicitly — never assume a cascade.
        """
        if await self.get(document_id, matter_id) is None:
            return 0
        result = await self.session.execute(
            delete(DocumentSection).where(DocumentSection.document_id == document_id)
        )
        return max(int(result.rowcount or 0), 0)

    async def delete_sections_for_matter(self, matter_id: int) -> int:
        """Bulk-delete every section row of one matter; rows gone."""
        result = await self.session.execute(
            delete(DocumentSection).where(DocumentSection.matter_id == matter_id)
        )
        return max(int(result.rowcount or 0), 0)
