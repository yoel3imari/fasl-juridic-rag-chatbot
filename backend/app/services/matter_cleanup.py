"""Hard-delete purge for documents and matters (rows + vectors + blobs).

Operation order is load-bearing — do not reorder:

1. Read everything needed inside one transaction (target rows,
   ``DocumentVersion.blob_ref`` strings), then release it: no network I/O and
   no file I/O while a transaction is open (same discipline as
   ``app.services.ingestion`` commits provenance before embedding).
2. Purge the Qdrant evidence points (worker thread — the store is sync).
3. Commit the DB delete.
4. Unlink the blob originals (worker thread), then prune the empty matter dir.

Rationale: vectors must never outlive their DB row — deleted legal text must
stop being searchable. A failure between steps 2 and 3 therefore leaves a row
that is present but unsearchable, a state a retried DELETE fixes cleanly
(step 2 is idempotent: it purges the now-zero remaining points, then the row
goes). The reverse order would leave searchable vectors behind with no row to
retry against.

``aiosqlite`` does not enable ``PRAGMA foreign_keys=ON``, and
``DocumentSection`` has no ORM relationship on ``Document``, so section rows
are bulk-deleted with explicit ``delete()`` statements — never assumed away
by a cascade.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.models.base import AsyncSession
from app.services import ingestion as ingestion_mod

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """Counts from one completed purge (documents, points, files removed)."""

    removed_documents: int
    removed_points: int
    removed_files: int


class EvidencePurger(Protocol):
    """Sync purge target; the real one wraps QdrantStore, tests fake it."""

    def delete_evidence(self, *, matter_id: int, document_id: int | None = None) -> int:
        """Remove matching evidence points; return how many were removed."""
        ...


class QdrantEvidencePurger:
    """Real Qdrant adapter; delegates to the shared hybrid store."""

    def __init__(self, url: str | None = None, local_path: str | None = None) -> None:
        from app.infrastructure.qdrant.store import QdrantStore

        self._store = QdrantStore(url=url, local_path=local_path)

    def delete_evidence(self, *, matter_id: int, document_id: int | None = None) -> int:
        """Remove evidence points by payload filter; return the count."""
        return self._store.delete_evidence(matter_id=matter_id, document_id=document_id)


def get_purger() -> EvidencePurger:
    """Factory seam: tests override this with an in-memory fake."""
    return QdrantEvidencePurger()  # type: ignore[return-value]


async def purge_document(
    session: AsyncSession, *, matter_id: int, document_id: int
) -> PurgeResult | None:
    """Hard-delete one document inside one matter; None when it does not exist.

    Order: read in-transaction → purge points → commit rows → unlink blobs.
    """
    from anyio import to_thread

    from app.repositories.document import DocumentRepository

    repo = DocumentRepository(session)
    if await repo.get(document_id, matter_id) is None:
        return None
    blob_refs = await repo.blob_refs(document_id, matter_id)
    # Reads done: release the transaction before any network or file I/O.
    await session.commit()

    purger = get_purger()
    removed_points = await to_thread.run_sync(
        lambda: purger.delete_evidence(matter_id=matter_id, document_id=document_id)
    )

    try:
        # Explicit bulk delete: no ORM relationship covers sections and
        # aiosqlite FKs are off (see module docstring).
        await repo.delete_sections(document_id, matter_id)
        await repo.delete(document_id, matter_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    removed_files = await to_thread.run_sync(lambda: _unlink_blobs(blob_refs, matter_id=matter_id))
    return PurgeResult(
        removed_documents=1,
        removed_points=removed_points,
        removed_files=removed_files,
    )


async def purge_matter(session: AsyncSession, *, matter_id: int) -> PurgeResult | None:
    """Hard-delete one matter and everything under it; None when absent.

    Order: read in-transaction → purge points → commit rows → unlink blobs.
    """
    from anyio import to_thread

    from app.repositories.document import DocumentRepository
    from app.repositories.matter import MatterRepository

    matter_repo = MatterRepository(session)
    doc_repo = DocumentRepository(session)
    if await matter_repo.get(matter_id) is None:
        return None
    removed_documents = await doc_repo.count_for_matter(matter_id)
    blob_refs = await doc_repo.blob_refs_for_matter(matter_id)
    # Reads done: release the transaction before any network or file I/O.
    await session.commit()

    purger = get_purger()
    removed_points = await to_thread.run_sync(lambda: purger.delete_evidence(matter_id=matter_id))

    try:
        # Sections have no relationship on Matter/Document; bulk delete first.
        await doc_repo.delete_sections_for_matter(matter_id)
        # ORM cascade removes documents (+versions), conversations (+messages),
        # analyses and drafts.
        await matter_repo.delete(matter_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    removed_files = await to_thread.run_sync(lambda: _unlink_blobs(blob_refs, matter_id=matter_id))
    return PurgeResult(
        removed_documents=removed_documents,
        removed_points=removed_points,
        removed_files=removed_files,
    )


def _unlink_blobs(blob_refs: Sequence[str], *, matter_id: int) -> int:
    """Unlink blob originals under the storage root; prune the matter dir.

    Paths that resolve outside the storage root are skipped, never unlinked.
    Only files that actually existed and were removed are counted.
    """
    root = ingestion_mod.get_storage_dir()
    root_resolved = root.resolve()
    removed = 0
    for ref in blob_refs:
        # `root / abs_path` yields the absolute path; resolve() then decides.
        target = (root / ref).resolve()
        if not target.is_relative_to(root_resolved):
            logger.warning("skipping blob outside storage root: %r", ref)
            continue
        try:
            target.unlink()
        except FileNotFoundError:
            continue  # already gone: not removed by this purge
        except OSError as exc:
            logger.warning("failed to unlink blob %s: %s", target, exc)
            continue
        removed += 1
    _prune_matter_dir(root_resolved / f"matter_{matter_id}")
    return removed


def _prune_matter_dir(matter_dir: Path) -> None:
    """Remove the matter directory when (and only when) it is empty."""
    try:
        matter_dir.rmdir()
    except FileNotFoundError:
        logger.debug("matter dir already gone: %s", matter_dir)
    except OSError as exc:
        # Non-empty or unreadable: leave it — never recurse, never force.
        logger.debug("matter dir not pruned (%s): %s", matter_dir, exc)
