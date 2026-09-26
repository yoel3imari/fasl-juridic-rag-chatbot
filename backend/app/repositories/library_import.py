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

from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library_import import (
    LibraryImportChunk,
    LibraryImportFile,
    LibraryImportRun,
)

# Stage columns reported in `by_status`, in the order the coverage payload uses.
LEDGER_STAGES: tuple[str, ...] = (
    "parse_status",
    "extract_status",
    "embed_status",
    "index_status",
)


def zero_ledger_summary() -> dict[str, Any]:
    """The all-zero aggregate. Key order is part of the coverage payload."""
    return {
        "totals": {
            "files": 0,
            "indexed": 0,
            "extracted": 0,
            "embedded": 0,
            "chunks": 0,
            "chunks_indexed": 0,
        },
        "by_category": {},
        "by_status": {},
        "by_edition": {},
    }


async def _single_row(cursor: Any) -> Any:
    """Fetch the one row of an aggregate query.

    The queries below are COUNT/SUM aggregates with no GROUP BY, so SQLite always
    returns exactly one row. The guard exists only to satisfy the type checker;
    it is unreachable, and raising here still degrades to the zero summary
    because every caller wraps this in try/except.
    """
    row = await cursor.fetchone()
    if row is None:  # pragma: no cover - unreachable for aggregate queries
        raise RuntimeError("aggregate ledger query returned no row")
    return row


async def read_ledger_summary(db_path: Path) -> dict[str, Any]:
    """Aggregate file counts from the SQLite ledger (read-only, pure read).

    Async, so a coverage request never blocks the event loop on a second
    SQLite connection. Raises when the ledger is missing or unreadable; the
    caller converts that into a zero summary plus a gap, never a 500.
    """
    import aiosqlite

    summary = zero_ledger_summary()
    uri = f"file:{db_path}?mode=ro"
    async with aiosqlite.connect(uri, uri=True) as con:
        totals = summary["totals"]
        cursor = await con.execute("SELECT COUNT(*) FROM library_import_files")
        totals["files"] = (await _single_row(cursor))[0]
        for stage in LEDGER_STAGES:
            # `stage` is a module constant, never request input.
            cursor = await con.execute(
                f"SELECT {stage}, COUNT(*) FROM library_import_files GROUP BY {stage}"  # noqa: S608
            )
            rows = await cursor.fetchall()
            summary["by_status"][stage] = {s or "unknown": n for s, n in rows}
        totals["indexed"] = summary["by_status"].get("index_status", {}).get("indexed", 0)
        totals["extracted"] = summary["by_status"].get("extract_status", {}).get("extracted", 0)
        totals["embedded"] = summary["by_status"].get("embed_status", {}).get("embedded", 0)
        for label, cleaned in (
            ("category", "unparsed"),
            ("edition", "unparsed"),
        ):
            # `label` is a literal from the tuple above, never request input.
            cursor = await con.execute(
                f"SELECT {label}, COUNT(*) FROM library_import_files GROUP BY {label}"  # noqa: S608
            )
            rows = await cursor.fetchall()
            summary[f"by_{label}"] = {(v or cleaned): n for v, n in rows}
        cursor = await con.execute(
            "SELECT COALESCE(SUM(chunk_count), 0), "
            "COALESCE(SUM(indexed_count), 0) FROM library_import_files"
        )
        row = await _single_row(cursor)
        totals["chunks"] = int(row[0])
        totals["chunks_indexed"] = int(row[1])
    return summary


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
