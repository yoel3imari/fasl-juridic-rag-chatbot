"""Library-import ledger repository: all `library_import_*` data access.

The ledger is the most safety-critical data access in the repo: bulk runs are
resumable, every stage commits per file so a kill mid-run loses at most one
file, and `indexed_count` is maintained with single atomic statements so two
writers cannot lose an update.

This module owns the queries. The per-file `await session.commit()` boundaries
stay with the callers — they are load-bearing for resume, so the repository
never commits.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library_import import (
    LibraryImportChunk,
    LibraryImportFile,
    LibraryImportRun,
)


class LibraryImportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def latest_run(self, kind: str) -> LibraryImportRun | None:
        """Newest `library_import_runs` row for `kind`, or None."""
        return (
            (
                await self.session.execute(
                    select(LibraryImportRun)
                    .where(LibraryImportRun.kind == kind)
                    .order_by(LibraryImportRun.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    async def list_files(self) -> list[LibraryImportFile]:
        """Every ledger file row, ordered by path."""
        return list(
            (await self.session.execute(select(LibraryImportFile).order_by(LibraryImportFile.path)))
            .scalars()
            .all()
        )

    async def get_file(self, file_id: int) -> LibraryImportFile | None:
        return await self.session.get(LibraryImportFile, file_id)

    async def find_file_by_path(self, path: str) -> LibraryImportFile | None:
        """The ledger row for one relative path, or None."""
        return (
            await self.session.execute(
                select(LibraryImportFile).where(LibraryImportFile.path == path)
            )
        ).scalar_one_or_none()

    async def list_chunks(self, file_id: int) -> list[LibraryImportChunk]:
        """Chunks for one file, unordered (embed stage)."""
        return list(
            (
                await self.session.execute(
                    select(LibraryImportChunk).where(LibraryImportChunk.file_id == file_id)
                )
            )
            .scalars()
            .all()
        )

    async def list_chunks_ordered(self, file_id: int) -> list[LibraryImportChunk]:
        """Chunks for one file, in `ord` order (index stage)."""
        return list(
            (
                await self.session.execute(
                    select(LibraryImportChunk)
                    .where(LibraryImportChunk.file_id == file_id)
                    .order_by(LibraryImportChunk.ord)
                )
            )
            .scalars()
            .all()
        )

    def new_pending_chunk(self, chunk_id: str, file_id: int, ord_: int) -> LibraryImportChunk:
        """Stage a not-yet-embedded chunk row. The caller adds and flushes."""
        return LibraryImportChunk(chunk_id=chunk_id, file_id=file_id, ord=ord_, status="pending")

    async def bump_indexed_count(self, file_id: int, delta: int = 1) -> None:
        """Atomically increment `indexed_count` with a single UPDATE statement.

        Never read-modify-write this counter: a single statement keeps the
        increment atomic under SQLite's writer serialization, so two concurrent
        writers cannot lose an update.
        """
        await self.session.execute(
            update(LibraryImportFile)
            .where(LibraryImportFile.id == file_id)
            .values(indexed_count=LibraryImportFile.indexed_count + delta)
        )
