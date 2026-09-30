"""Matter registry routes: create, list, get, partial update, hard delete.

Matter CRUD beyond create/list: GET one, PATCH supplied fields only, and
DELETE with a full purge of documents/sections/conversations/analyses/drafts
plus evidence points and blob originals (app.services.matter_cleanup).
No auth, no soft delete — a deleted matter is gone.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError, map_error
from app.models.base import get_db
from app.models.matter import Matter
from app.repositories.matter import MatterRepository
from app.schemas.matters import MatterCreateIn, MatterOut, MatterUpdateIn
from app.services.matter_cleanup import purge_matter

router = APIRouter(prefix="/api/v1/matters", tags=["matters"])


def _matter_out(row: Matter) -> MatterOut:
    """Shape a stored matter row onto the frozen response model."""
    return MatterOut(
        id=row.id,
        title=row.title,
        matter_type=row.matter_type,
        jurisdiction=row.jurisdiction,
        language=row.language,
    )


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


@router.get("/{matter_id}", response_model=MatterOut)
async def get_matter(
    matter_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MatterOut:
    """Fetch one matter by id; 404 when it does not exist."""
    row = await MatterRepository(session).get(matter_id)
    if row is None:
        raise HTTPException(*map_error(NotFoundError("matter not found")))
    return _matter_out(row)


@router.patch("/{matter_id}", response_model=MatterOut)
async def update_matter(
    matter_id: int,
    body: MatterUpdateIn,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> MatterOut:
    """Apply only the fields present in the body; 404 when the matter is absent."""
    fields = {
        key: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if value is not None
    }
    try:
        row = await MatterRepository(session).update(matter_id, fields)
        if row is None:
            raise HTTPException(*map_error(NotFoundError("matter not found")))
        out = _matter_out(row)
        await session.commit()
    except HTTPException:
        await session.rollback()
        raise
    except Exception as exc:
        await session.rollback()
        raise HTTPException(*map_error(exc, context="matter update failed")) from exc
    return out


@router.delete("/{matter_id}", status_code=status.HTTP_200_OK)
async def delete_matter(
    matter_id: int,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Hard-delete a matter and everything scoped to it, purging vectors/blobs."""
    result = await purge_matter(session, matter_id=matter_id)
    if result is None:
        raise HTTPException(*map_error(NotFoundError("matter not found")))
    return {
        "status": "deleted",
        "id": matter_id,
        "removed_documents": result.removed_documents,
        "removed_points": result.removed_points,
        "removed_files": result.removed_files,
    }
