"""GET/POST /api/v1/search: dual-domain hybrid search with matter isolation.

domain=matter|authority|both. Matter-involving searches REQUIRE matter_id.
``both`` returns SEPARATE labeled lists (matter + authority), never merged.
Qdrant down → 503 with retry hint; CrispEmbed down → 503.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.search import service as svc

router = APIRouter(prefix="/api/v1/search", tags=["search"])

Domain = Literal["matter", "authority", "both"]


class SearchBody(BaseModel):
    query: str = Field(min_length=1)
    domain: Domain = "both"
    matter_id: int | None = None
    top_k: int = Field(default=5, ge=1, le=30)


def get_store() -> svc.Store:
    """Factory seam: Qdrant store from settings (URL or local path)."""
    from app.search.store import QdrantStore

    return QdrantStore()  # type: ignore[return-value]


def get_embedder() -> svc.Embedder:
    """Factory seam: CrispEmbed bge-m3 HTTP client."""
    from app.library.embedder import CrispEmbedClient

    return CrispEmbedClient()  # type: ignore[return-value]


def _require_matter(domain: str, matter_id: int | None) -> int:
    if domain in ("matter", "both") and matter_id is None:
        raise HTTPException(
            status_code=422,
            detail="matter_id is required for matter-domain search",
        )
    return int(matter_id) if matter_id is not None else -1


async def _dispatch(domain: Domain, matter_id: int | None, query: str, top_k: int):
    store = get_store()
    embedder = get_embedder()
    try:
        if domain == "matter":
            return await svc.search_matter(
                store=store,
                embedder=embedder,
                matter_id=int(matter_id),  # type: ignore[arg-type]
                query=query,
                top_k=top_k,
            )
        if domain == "authority":
            return await svc.search_authority(
                store=store, embedder=embedder, query=query, top_k=top_k
            )
        return await svc.search_both(
            store=store,
            embedder=embedder,
            matter_id=int(matter_id),  # type: ignore[arg-type]
            query=query,
            top_k=top_k,
        )
    except svc.EmbeddingUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{exc} — retry shortly",
        ) from exc
    except svc.SearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("")
async def search_post(body: SearchBody) -> dict:
    """POST search with JSON body."""
    _require_matter(body.domain, body.matter_id)
    return await _dispatch(body.domain, body.matter_id, body.query, body.top_k)


@router.get("")
async def search_get(
    query: str = Query(min_length=1),
    domain: Domain = Query(default="both"),
    matter_id: int | None = Query(default=None),
    top_k: int = Query(default=5, ge=1, le=30),
) -> dict:
    """GET search with query params."""
    _require_matter(domain, matter_id)
    return await _dispatch(domain, matter_id, query, top_k)
