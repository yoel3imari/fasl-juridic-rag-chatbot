"""Invalidate every artifact + vector cache after a pipeline change.

Why this exists
---------------
The pipeline fingerprint (``app.cli.library.fingerprint``) already detects
that extraction/chunking/embedding logic changed, but nothing acted on that
detection for the artifacts themselves. Artifacts are keyed by PDF content
hash, NOT by fingerprint, so after a chunker change every artifact on disk is
stale while the ledger still reports the file as ``extracted`` +
``embedded`` + ``indexed``. Worse, ``chunk_id`` hashes
``(source, version, edition, article, text, page, ordinal)``, so a chunker or
provenance change invalidates EVERY chunk id -- the corresponding Qdrant
points become orphans that still match searches and cite stale text.

This command makes that invalidation explicit and auditable instead of
implicit. It is destructive to derived state only:

* derived (regenerable): ``library_import_chunks`` rows, the zstd artifacts,
  and the per-file embed vector caches;
* authoritative (never touched): the PDFs, and the catalog provenance
  (``path``/``sha256``/``bytes``/``pages``/``category``/``source``/
  ``version``/``edition``/``parse_status``).

Deliberately bypasses :func:`app.cli.library.bulk_state.set_file_stage_status`
and its no-regression rule: regressing ``extract_status`` from the terminal
``extracted`` back to ``pending`` is exactly the point of an explicit
invalidation, and the normal stage helpers must never do it implicitly.

Requires ``--confirm``. Writes a ``library_import_runs`` sentinel row
(``kind="invalidate"``) carrying the live pipeline fingerprint, so the ledger
records which fingerprint the NEXT artifacts will belong to.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register ledger metadata)
from app.cli.library.artifacts import artifact_path_for
from app.cli.library.bulk_run import EMBED_CACHE_SUFFIX
from app.cli.library.bulk_state import single_writer
from app.cli.library.fingerprint import compute_pipeline_fingerprint
from app.models.base import Base
from app.models.library_import import LibraryImportChunk, LibraryImportRun
from app.repositories.library_import import LibraryImportRepository


class InvalidationRefused(Exception):
    """Refused to invalidate without explicit operator consent."""


def select_invalidation_targets(rows: Any) -> list[int]:
    """File ids whose derived state must be rebuilt (pure selection).

    A file is a target when it has EVER been extracted: either it still
    carries an artifact sha, or its extract stage reached ``extracted``. Rows
    that never produced an artifact (duplicates, failed, still pending) are
    left alone -- there is nothing derived to discard.
    """
    out: list[int] = []
    for row in rows:
        if getattr(row, "artifact_sha256", None):
            out.append(int(row.id))
        elif getattr(row, "extract_status", None) == "extracted":
            out.append(int(row.id))
    return out


async def invalidate(
    db_url: str,
    *,
    artifact_dir: str | Path,
    confirm: bool,
    limit: int | None = None,
) -> dict[str, Any]:
    """Discard all derived artifact state so the next extract rebuilds it."""
    if not confirm:
        raise InvalidationRefused(
            "refusing to invalidate without --confirm (this discards every "
            "artifact, embed cache and chunk row)"
        )
    adir = Path(artifact_dir)
    engine = create_async_engine(db_url, connect_args={"timeout": 30})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        with single_writer():
            async with maker() as session:
                repo = LibraryImportRepository(session)
                rows = await repo.list_files()
                targets = select_invalidation_targets(rows)
                if limit is not None:
                    if limit < 0:
                        raise ValueError(f"limit must be >= 0, got {limit}")
                    targets = targets[:limit]
                target_set = set(targets)
                files_reset = 0
                chunks_deleted = 0
                artifacts_removed = 0
                caches_removed = 0
                for row in rows:
                    if int(row.id) not in target_set:
                        continue
                    result = await session.execute(
                        delete(LibraryImportChunk).where(LibraryImportChunk.file_id == row.id)
                    )
                    chunks_deleted += int(result.rowcount or 0)
                    # Direct attribute writes: this is a deliberate,
                    # operator-confirmed reset of terminal stage values, which
                    # set_file_stage_status must never do on its own.
                    row.extract_status = "pending"
                    row.embed_status = "pending"
                    row.index_status = "pending"
                    row.chunk_count = 0
                    row.indexed_count = 0
                    row.artifact_sha256 = None
                    row.quarantine_reason = None
                    files_reset += 1
                    # The embed cache MUST go too: ensure_embed seeds its
                    # cache from disk and writes the MERGED dict back, so a
                    # stale cache would be re-persisted forever and grow
                    # without bound as chunk ids churn.
                    for path, counter in (
                        (artifact_path_for(adir, row.sha256), "artifact"),
                        (adir / f"{row.sha256}{EMBED_CACHE_SUFFIX}", "cache"),
                    ):
                        if path.exists():
                            path.unlink()
                            if counter == "artifact":
                                artifacts_removed += 1
                            else:
                                caches_removed += 1
                    await session.commit()
                fingerprint = ""
                try:
                    from app.config import settings

                    fingerprint = compute_pipeline_fingerprint(
                        model=settings.EMBEDDING_MODEL, dim=int(settings.EMBEDDING_DIM)
                    )
                    session.add(
                        LibraryImportRun(
                            kind="invalidate",
                            model="",
                            dim=0,
                            git_sha=fingerprint,
                            status="done",
                            payload_json={
                                "files_reset": files_reset,
                                "chunks_deleted": chunks_deleted,
                                "artifacts_removed": artifacts_removed,
                                "caches_removed": caches_removed,
                            },
                        )
                    )
                    await session.commit()
                except Exception:  # never fail the reset on the sentinel
                    await session.rollback()
    finally:
        await engine.dispose()
    return {
        "files_reset": files_reset,
        "chunks_deleted": chunks_deleted,
        "artifacts_removed": artifacts_removed,
        "caches_removed": caches_removed,
        "pipeline_fingerprint": fingerprint,
    }


def add_invalidate_parser(sub: Any) -> argparse.ArgumentParser:
    """Register `bulk invalidate` (requires --confirm)."""
    parser = sub.add_parser(
        "invalidate",
        help="discard all artifacts/embed caches/chunk rows after a pipeline change",
    )
    parser.add_argument("--db-url", default=None)
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="required: acknowledge discarding every derived artifact",
    )
    parser.add_argument("--limit", type=int, default=None)
    return parser


async def run_invalidate_from_args(args: argparse.Namespace) -> dict[str, Any]:
    from app.config import settings

    return await invalidate(
        args.db_url or settings.DATABASE_URL,
        artifact_dir=args.artifact_dir or settings.LIBRARY_ARTIFACT_DIR,
        confirm=args.confirm,
        limit=args.limit,
    )
