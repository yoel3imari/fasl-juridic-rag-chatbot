"""Re-embed existing matter documents at the migrated dim (plan todo 9).

``python -m app.library.bulk reembed-matter`` regenerates dense embeddings
for every existing matter document section through the CURRENT model
(``settings.EMBEDDING_MODEL``) and upserts with the SAME point ids the
ingestion pipeline used (``f"{document_id}:{section_id}"``, UUID5-mapped
inside :class:`app.infrastructure.qdrant.store.QdrantStore`), then reconciles counts.

Matter-domain logic, point ids, payload shape, and collection filters are
untouched -- only the dense vector dim changes. No rows in
``matters``/``documents``/``document_sections`` are ever mutated; a
mid-run failure writes a ``failed`` sentinel and never a ``done`` one, so
rows are never marked migrated on a partial run. The pre-migration Qdrant
snapshots (todo 8) stay restorable: this command never deletes anything.

In ALL cases -- including the zero-matter no-op -- a
``library_import_runs`` row (kind=``reembed-matter``) is written with
``{model, dim, git_sha, counts}`` so the T16 preflight never deadlocks on
an empty matter table.

Exit codes: 0 on success (including the no-op), 2 (``FAIL_EXIT``) on any
embedding/upsert/reconciliation failure.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

FAIL_EXIT = 2

EVIDENCE_COLLECTION = "matter_evidence"
AUTHORITY_COLLECTION = "legal_authorities"
COUNTED_COLLECTIONS = (EVIDENCE_COLLECTION, AUTHORITY_COLLECTION)


class ReembedFailed(Exception):
    """Embedding/upsert/reconciliation failure: exit non-zero, no done row."""


def _client_for(url: str, local_path: str | None = None):
    from qdrant_client import QdrantClient

    if local_path:
        return QdrantClient(path=local_path)
    return QdrantClient(url=url.rstrip("/"), timeout=30)


def _read_client(url: str, local_path: str | None) -> tuple[Any, bool]:
    """Client for count/scroll reads + whether the caller must close it.

    Local-mode dirs are file-locked to ONE client instance, so reads reuse
    the store's shared per-path client (never closed); remote clients stay
    short-lived per call.
    """
    if local_path:
        from app.infrastructure.qdrant.store import _shared_local_client

        return _shared_local_client(local_path), False
    return _client_for(url), True


def _doc_filter(document_id: int):
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    return Filter(
        must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
    )


def _collection_missing(exc: Exception) -> bool:
    """True if the error is just 'collection does not exist yet'."""
    if isinstance(exc, ValueError) and "not found" in str(exc):
        return True  # qdrant local mode
    status = getattr(exc, "status_code", None)
    return status == 404  # remote server


def collection_count(client: Any, name: str) -> int:
    """Exact live point count (read-only; assertions use reads, not prose).

    A not-yet-created collection reads as 0 (fresh local dirs in tests);
    any other transport error raises.
    """
    try:
        return int(client.count(name, exact=True).count)
    except Exception as exc:
        if _collection_missing(exc):
            return 0
        raise


def live_dense_dim(client: Any, name: str) -> int | None:
    """Live dense dim, or None if the collection does not exist yet.

    A missing collection is created at the embed dim by the store, so the
    dim check is skipped in that case only.
    """
    try:
        info = client.get_collection(name)
    except Exception as exc:
        if _collection_missing(exc):
            return None
        raise
    vectors = info.config.params.vectors
    if isinstance(vectors, dict):
        dense = vectors["dense"]
    else:
        dense = vectors.dense if hasattr(vectors, "dense") else vectors
    return int(dense.size if hasattr(dense, "size") else dense["size"])


async def _load_matter_sections(db_url: str) -> list[dict[str, Any]]:
    """Load every document with its sections (ordered); touches nothing."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401  (register matter + ledger metadata)
    from app.models.base import Base
    from app.models.document import Document
    from app.models.document_section import DocumentSection

    engine = create_async_engine(db_url, connect_args={"timeout": 30})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            docs = (
                (await session.execute(select(Document).order_by(Document.id)))
                .scalars()
                .all()
            )
            out: list[dict[str, Any]] = []
            for doc in docs:
                secs = (
                    (
                        await session.execute(
                            select(DocumentSection)
                            .where(DocumentSection.document_id == doc.id)
                            .order_by(DocumentSection.id)
                        )
                    )
                    .scalars()
                    .all()
                )
                out.append(
                    {
                        "document_id": int(doc.id),
                        "matter_id": int(doc.matter_id),
                        "doc_type": str(doc.doc_type),
                        "status": str(doc.status),
                        "sections": [
                            {
                                "section_id": s.section_id,
                                "page_start": s.page_start,
                                "span_start": s.span_start,
                                "span_end": s.span_end,
                                "normalized_text": s.normalized_text,
                            }
                            for s in secs
                        ],
                    }
                )
            return out
    finally:
        await engine.dispose()


def _evidence_points(
    *,
    matter_id: int,
    document_id: int,
    doc_type: str,
    sections: list[dict[str, Any]],
    vectors: list[list[float]],
) -> list[Any]:
    """Build points with the pipeline's EXACT id/payload shape (same ids).

    Mirrors ``app.ingestion.pipeline._embed_and_index``: raw id
    ``f"{document_id}:{section_id}"`` (UUID5 mapping lives in the store),
    identical payload keys. Only the dense vector content/dim is new.
    """
    from app.domain.search.schemas import EvidencePoint

    if len(vectors) != len(sections):
        raise ReembedFailed(
            "embedding count mismatch: "
            f"{len(vectors)} vectors for {len(sections)} sections "
            f"(document_id={document_id})"
        )
    return [
        EvidencePoint(
            id=f"{document_id}:{s['section_id']}",
            vector=vec,
            matter_id=matter_id,
            document_id=document_id,
            version_no=1,
            doc_type=doc_type,
            page=s["page_start"],
            span=[s["span_start"], s["span_end"]],
            faithful_ref=s["section_id"],
            text=s["normalized_text"],
        )
        for s, vec in zip(sections, vectors, strict=True)
    ]


async def run_reembed(args: argparse.Namespace) -> dict[str, Any]:
    """Execute ``reembed-matter``; raise ReembedFailed on any failure."""
    from app.config import settings
    from app.library.bulk_migrate import live_git_sha, write_sentinel

    model = settings.EMBEDDING_MODEL
    dim = int(settings.EMBEDDING_DIM)
    sha = live_git_sha()
    qdrant_url = args.qdrant_url or settings.QDRANT_URL
    local_path = args.qdrant_local_path or (settings.QDRANT_LOCAL_PATH or None)
    db_url = args.db_url or settings.DATABASE_URL
    embed_url = args.embed_url or settings.CRISPEMBED_URL

    client, close_client = _read_client(qdrant_url, local_path)
    try:
        pre = {name: collection_count(client, name) for name in COUNTED_COLLECTIONS}
        live_dim = live_dense_dim(client, EVIDENCE_COLLECTION)
    finally:
        if close_client:
            client.close()
    if live_dim is not None and live_dim != dim:
        raise ReembedFailed(
            f"live {EVIDENCE_COLLECTION} dim={live_dim} != configured {dim} "
            f"(run `bulk migrate --confirm` first; refusing)"
        )

    docs = await _load_matter_sections(db_url)
    n_sections = sum(len(d["sections"]) for d in docs)

    if not docs or n_sections == 0:
        from app.library.bulk_migrate import write_sentinel as _ws

        row_id = await _ws(
            db_url,
            kind="reembed-matter",
            model=model,
            dim=dim,
            sha=sha,
            status="done",
            payload={
                "noop": True,
                "note": "no matter rows to re-embed",
                "documents": len(docs),
                "sections": n_sections,
                "counts": {"pre": pre, "post": dict(pre)},
            },
        )
        print(
            f"reembed-matter: no matter rows in db "
            f"(documents={len(docs)} sections={n_sections}); "
            f"counts unchanged matter_evidence={pre[EVIDENCE_COLLECTION]} "
            f"legal_authorities={pre[AUTHORITY_COLLECTION]}; "
            f"wrote reembed-matter sentinel id={row_id}"
        )
        return {
            "action": "reembed-matter",
            "noop": True,
            "model": model,
            "dim": dim,
            "git_sha": sha,
            "pre": pre,
            "post": dict(pre),
            "documents": 0,
            "sections": 0,
            "sentinel_id": row_id,
        }

    # --- Phase 1: embed EVERYTHING before any upsert ---------------------
    # A mid-run embedding failure therefore leaves zero Qdrant writes and
    # zero row changes; only a `failed` sentinel is recorded.
    from app.infrastructure.embeddings.client import CrispEmbedClient

    embedder = CrispEmbedClient(base_url=embed_url, model=model)
    embedded: list[tuple[dict[str, Any], list[list[float]]]] = []
    try:
        for doc in docs:
            texts = [s["normalized_text"] for s in doc["sections"]]
            vectors = embedder.embed_sync(texts)
            for vec in vectors:
                if len(vec) != dim:
                    raise ReembedFailed(
                        f"embed dim {len(vec)} != configured {dim} "
                        f"(document_id={doc['document_id']}; refusing)"
                    )
            embedded.append((doc, vectors))
    except ReembedFailed:
        raise
    except Exception as exc:
        row_id = await write_sentinel(
            db_url,
            kind="reembed-matter",
            model=model,
            dim=dim,
            sha=sha,
            status="failed",
            payload={
                "error": f"embedding failed: {exc}",
                "documents": len(docs),
                "sections": n_sections,
                "counts": {"pre": pre},
            },
        )
        print(
            f"FAIL: reembed-matter embedding failed: {exc} "
            f"(sentinel id={row_id}; rows unmigrated, snapshot restorable)",
            file=sys.stderr,
        )
        raise ReembedFailed(f"embedding failed: {exc}") from exc

    # --- Phase 2: upsert with SAME point ids, then reconcile -------------
    from app.infrastructure.qdrant.store import QdrantStore

    store = QdrantStore(url=qdrant_url, local_path=local_path, dim=dim)
    per_document: dict[str, int] = {}
    try:
        for doc, vectors in embedded:
            points = _evidence_points(
                matter_id=doc["matter_id"],
                document_id=doc["document_id"],
                doc_type=doc["doc_type"],
                sections=doc["sections"],
                vectors=vectors,
            )
            stored = store.upsert_evidence(points)
            if stored != len(points):
                raise ReembedFailed(
                    f"upsert short for document_id={doc['document_id']}: "
                    f"{stored} != {len(points)}"
                )
            per_document[str(doc["document_id"])] = stored
    except ReembedFailed:
        raise
    except Exception as exc:
        row_id = await write_sentinel(
            db_url,
            kind="reembed-matter",
            model=model,
            dim=dim,
            sha=sha,
            status="failed",
            payload={
                "error": f"upsert failed: {exc}",
                "documents": len(docs),
                "sections": n_sections,
                "counts": {"pre": pre},
                "per_document": per_document,
            },
        )
        print(
            f"FAIL: reembed-matter upsert failed: {exc} "
            f"(sentinel id={row_id}; rows unmigrated, snapshot restorable)",
            file=sys.stderr,
        )
        raise ReembedFailed(f"upsert failed: {exc}") from exc

    # --- Phase 3: reconcile (per-document counts + payload spot-check) ---
    client, close_client = _read_client(qdrant_url, local_path)
    try:
        post = {name: collection_count(client, name) for name in COUNTED_COLLECTIONS}
        for doc in docs:
            doc_id = doc["document_id"]
            got = int(
                client.count(
                    EVIDENCE_COLLECTION,
                    count_filter=_doc_filter(doc_id),
                    exact=True,
                ).count
            )
            want = len(doc["sections"])
            if got != want:
                raise ReembedFailed(
                    f"reconcile failed for document_id={doc_id}: "
                    f"qdrant={got} != sections={want}"
                )
            # Spot-check: matter_id/document_id payloads intact.
            recs, _ = client.scroll(
                EVIDENCE_COLLECTION,
                scroll_filter=_doc_filter(doc_id),
                limit=1,
                with_payload=True,
            )
            if not recs:
                raise ReembedFailed(
                    f"reconcile failed for document_id={doc_id}: "
                    f"scroll returned no points"
                )
            payload = dict(recs[0].payload or {})
            if (
                payload.get("matter_id") != doc["matter_id"]
                or payload.get("document_id") != doc_id
            ):
                raise ReembedFailed(
                    f"payload mismatch for document_id={doc_id}: "
                    f"{ {k: payload.get(k) for k in ('matter_id', 'document_id')} }"
                )
    finally:
        if close_client:
            client.close()

    row_id = await write_sentinel(
        db_url,
        kind="reembed-matter",
        model=model,
        dim=dim,
        sha=sha,
        status="done",
        payload={
            "model": model,
            "dim": dim,
            "git_sha": sha,
            "counts": {"pre": pre, "post": post, "per_document": per_document},
        },
    )
    print(
        f"reembed-matter: matter_evidence pre={pre[EVIDENCE_COLLECTION]} "
        f"post={post[EVIDENCE_COLLECTION]}; legal_authorities "
        f"pre={pre[AUTHORITY_COLLECTION]} post={post[AUTHORITY_COLLECTION]}; "
        f"documents={len(docs)} sections={n_sections}; sentinel id={row_id}"
    )
    return {
        "action": "reembed-matter",
        "noop": False,
        "model": model,
        "dim": dim,
        "git_sha": sha,
        "pre": pre,
        "post": post,
        "documents": len(docs),
        "sections": n_sections,
        "per_document": per_document,
        "sentinel_id": row_id,
    }


def add_reembed_parser(sub: Any) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        "reembed-matter",
        help="re-embed existing matter sections at the current dim (same point ids)",
    )
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-local-path", default=None)
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--embed-url", default=None)
    return parser


async def run_reembed_from_args(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return await run_reembed(args)
    except ReembedFailed as exc:
        print(f"FAIL: reembed-matter refused: {exc}", file=sys.stderr)
        raise SystemExit(FAIL_EXIT)
