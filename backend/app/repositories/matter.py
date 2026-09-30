"""Matter repository: CRUD scoped to matters."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matter import Matter

UPDATABLE_FIELDS: frozenset[str] = frozenset({"title", "matter_type", "jurisdiction", "language"})


class MatterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, title: str, matter_type: str, jurisdiction: str, language: str
    ) -> Matter:
        matter = Matter(
            title=title,
            matter_type=matter_type,
            jurisdiction=jurisdiction,
            language=language,
        )
        self.session.add(matter)
        await self.session.flush()
        return matter

    async def get(self, matter_id: int) -> Matter | None:
        return await self.session.get(Matter, matter_id)

    async def list(self) -> list[Matter]:
        result = await self.session.execute(select(Matter).order_by(Matter.id))
        return list(result.scalars().all())

    async def update(self, matter_id: int, fields: dict[str, Any]) -> Matter | None:
        """Apply only the supplied fields; None when the matter does not exist.

        Absent keys are left untouched — defaults never overwrite stored rows.
        """
        matter = await self.session.get(Matter, matter_id)
        if matter is None:
            return None
        unknown = set(fields) - UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"not updatable matter fields: {sorted(unknown)}")
        for key, value in fields.items():
            setattr(matter, key, value)
        await self.session.flush()
        return matter

    async def delete(self, matter_id: int) -> bool:
        matter = await self.session.get(Matter, matter_id)
        if matter is None:
            return False
        await self.session.delete(matter)
        await self.session.flush()
        return True
