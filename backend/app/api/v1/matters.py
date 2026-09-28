"""Matter registry routes (task 10 gap-fill): create + list matters.

No matter CRUD endpoint existed — uploads, analysis, drafts, and chat all
take a matter_id path, so the app was unusable without one. Minimal scope:
create and list only; no auth, no deletion.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import map_error
from app.models.base import get_db
from app.repositories.matter import MatterRepository

router = APIRouter(prefix="/api/v1/matters", tags=["matters"])


class MatterCreateIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1, max_length=255)
    matter_type: str = Field(default="general", max_length=100)
    jurisdiction: str = Field(default="casablanca", max_length=100)
    language: str = Field(default="ar", max_length=10)


class MatterOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    matter_type: str
    jurisdiction: str
    language: str


@router.post("", response_model=MatterOut, status_code=status.HTTP_201_CREATED)
async def create_matter(
    body: MatterCreateIn, session: Annotated[AsyncSession, Depends(get_db)]
) -> MatterOut:
    """Create a matter row; returns its id for uploads/analysis/chat."""
    try:
        row = await MatterRepository(session).create(
            body.title, body.matter_type, body.jurisdiction, body.language
        )
        out = MatterOut(
            id=row.id,
            title=row.title,
            matter_type=row.matter_type,
            jurisdiction=row.jurisdiction,
            language=row.language,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise HTTPException(*map_error(exc, context="matter creation failed")) from exc
    return out


@router.get("", response_model=list[MatterOut])
async def list_matters(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> list[MatterOut]:
    """List all matters (single-user local app: no pagination needed)."""
    rows = await MatterRepository(session).list()
    return [
        MatterOut(
            id=r.id,
            title=r.title,
            matter_type=r.matter_type,
            jurisdiction=r.jurisdiction,
            language=r.language,
        )
        for r in rows
    ]
