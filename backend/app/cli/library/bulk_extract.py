"""Resumable extraction to zstd JSONL artifacts with OCR fallback + quarantine.

Contract (plan todo 12): reads catalogued rows from the T7 ledger, extracts
via the pure :mod:`app.cli.library.extract_worker` (PyMuPDF fast path + Tesseract
``ara+fra`` page fallback), chunks with the T11 chunker, and streams records
(:mod:`app.cli.library.artifacts`) to ``<dir>/<file_sha>.jsonl.zst``.

* Extract-eligible: ``parsed`` rows plus name-quarantined winners (filename
  unparseable, content is real PDF; fallback provenance already in the row).
  Skipped, never marked ``extracted``: content duplicates (``duplicate-of:…``),
  unreadable (``failed``), and ``pending`` rows.
* Bounded multiprocessing via ``settings.LIBRARY_EXTRACT_WORKERS`` (default 1
  = in-process; >= 2 prints a RAM warning and uses a process pool). Workers
  are pure (records over IPC, never SQLite). The parent is the SINGLE writer
  (``single_writer`` discipline) for artifacts + ledger, committing per file
  so a kill-mid-run resumes. Re-runs skip ``extracted`` rows with a present
  artifact and never touch ``quarantined`` rows.
* One bad file never crashes the run: corrupt/empty/OCR-failed files land at
  ``extract_status=quarantined`` with a reason, never ``extracted``.
* No ``UPLOAD_MAX_BYTES`` cap anywhere on this path (that cap is API-only).

T10 data quirks: the 14 name-quarantined winners ARE extract-eligible (the
quarantine is about the filename, not the content). The 18
``source@version#edition`` key collisions are harmless: artifact filenames key
by ``file_sha`` and ``chunk_id`` embeds the chunk-text hash, so same-key files
cannot overwrite each other. This stage writes no ``library_import_chunks``
rows (todo 14 owns the per-chunk ledger).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.cli.library.artifacts import artifact_path_for, sha256_file, write_artifact
from app.cli.library.bulk_state import single_writer
from app.cli.library.extract_worker import ExtractJob, ExtractResult, run_job

import app.models  # noqa: F401  (register ledger metadata)
from app.models.base import Base
from app.repositories.library_import import LibraryImportRepository


# Quarantine reasons that encode a retriable WORKER policy (not content
# failure): a newer worker may succeed, so these rows are re-driven instead
# of skipped. Corrupt/empty/OCR reasons stay terminal for this stage.
RETRIABLE_QUARANTINE_PREFIXES = ("chunking-failed:",)


def is_extract_eligible(
    parse_status: str, quarantine_reason: str | None
) -> tuple[bool, str | None]:
    """Decide extract eligibility from the catalog row.

    Returns ``(eligible, skip_reason)``. Name-quarantined winners (filename
    quarantine, real content) stay eligible; duplicates / failed / pending
    are skipped with a reason and must never become ``extracted``.
    """
    if parse_status == "parsed":
        return True, None
    if parse_status == "quarantined":
        if (quarantine_reason or "").startswith("duplicate-of:"):
            return False, quarantine_reason
        return True, None  # name quarantine: filename-only, content is real
    return False, f"parse_status={parse_status}"


def _collect_jobs(
    rows: Any,
    src: Path,
    adir: Path,
    min_chars: int,
    dpi: int,
    ocr_timeout: int,
    ocr_mode: str,
    limit: int | None,
) -> tuple[list[tuple[int, ExtractJob]], int, int]:
    """Filter ledger rows to ripe jobs in deterministic path order."""
    jobs: list[tuple[int, ExtractJob]] = []
    skipped_ineligible = 0
    skipped_done = 0
    for row in rows:
        eligible, _ = is_extract_eligible(row.parse_status, row.quarantine_reason)
        if not eligible:
            skipped_ineligible += 1
            continue
        if (
            row.extract_status == "extracted"
            and getattr(row, "artifact_sha256", None)
            and artifact_path_for(adir, row.sha256).exists()
        ):
            skipped_done += 1  # resume: never redo finished work
            continue
        if row.extract_status == "extracted":
            # Extracted but unverifiable (missing sha or missing artifact,
            # e.g. pre-todo-13 rows): re-drive, never silently pass.
            pass
        elif row.extract_status == "quarantined":
            if (row.quarantine_reason or "").startswith(RETRIABLE_QUARANTINE_PREFIXES):
                pass  # worker policy upgraded: re-drive this row
            else:
                skipped_done += 1  # terminal quarantine stays quarantined
                continue
        jobs.append(
            (
                row.id,
                ExtractJob(
                    rel=row.path,
                    abs_path=str(src / row.path),
                    source=row.source,
                    version=row.version,
                    edition=row.edition,
                    category=row.category or "",
                    file_sha=row.sha256,
                    min_chars=min_chars,
                    dpi=dpi,
                    ocr_timeout=ocr_timeout,
                    ocr_mode=ocr_mode,
                ),
            )
        )
    if limit is not None:
        if limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")
        jobs = jobs[:limit]  # post-dedup gating: winners already fixed by catalog
    return jobs, skipped_ineligible, skipped_done


async def run_extract(
    source_dir: str | Path | None = None,
    db_url: str | None = None,
    artifact_dir: str | Path | None = None,
    limit: int | None = None,
    workers: int | None = None,
) -> dict[str, Any]:
    """Extract eligible ledger rows to zstd artifacts (single-writer parent)."""
    src = (
        Path(source_dir)
        if source_dir is not None
        else Path(settings.LIBRARY_SOURCE_DIR)
    )
    url = db_url if db_url is not None else settings.DATABASE_URL
    adir = (
        Path(artifact_dir)
        if artifact_dir is not None
        else Path(settings.LIBRARY_ARTIFACT_DIR)
    )
    n_workers = settings.LIBRARY_EXTRACT_WORKERS if workers is None else workers
    if n_workers >= 2:
        print(
            f"WARNING: LIBRARY_EXTRACT_WORKERS={n_workers}: each worker holds a "
            "PyMuPDF document + 300-DPI page renders; prefer 1 on ~1.5GB hosts."
        )

    engine = create_async_engine(url, connect_args={"timeout": 30})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        with single_writer():
            async with maker() as session:
                repo = LibraryImportRepository(session)
                rows = await repo.list_files()
                jobs, skipped_ineligible, skipped_done = _collect_jobs(
                    rows,
                    src,
                    adir,
                    settings.LIBRARY_OCR_MIN_CHARS,
                    settings.LIBRARY_OCR_DPI,
                    settings.LIBRARY_OCR_TIMEOUT_SECONDS,
                    settings.LIBRARY_OCR_MODE,
                    limit,
                )
                report: dict[str, Any] = {
                    "extracted": 0,
                    "quarantined": 0,
                    "skipped": skipped_ineligible + skipped_done,
                    "chunks": 0,
                    "ocr_pages": 0,
                    "workers": n_workers,
                    "artifacts_dir": str(adir),
                }
                if n_workers >= 2:
                    from concurrent.futures import ProcessPoolExecutor

                    with ProcessPoolExecutor(max_workers=n_workers) as pool:
                        results: list[ExtractResult] = list(
                            pool.map(run_job, [j for _, j in jobs])
                        )
                else:
                    results = [run_job(j) for _, j in jobs]
                from app.cli.library.bulk_state import set_file_stage_status

                for (file_id, _), res in zip(jobs, results):
                    row = await repo.get_file(file_id)
                    if row is None:  # pragma: no cover - defensive
                        continue
                    if res.status == "extracted":
                        # Streaming write, one file at a time (never joined).
                        dest = artifact_path_for(adir, row.sha256)
                        write_artifact(iter(res.records), dest)
                        # Round-trip guard (todo 13): the ledger carries the
                        # artifact's sha256 so reads can fail LOUDLY on tamper.
                        row.artifact_sha256 = sha256_file(dest)
                        row.chunk_count = len(res.records)
                        row.quarantine_reason = None  # clear stale retry reason
                        set_file_stage_status(row, "extract_status", "extracted")
                        report["extracted"] += 1
                        report["chunks"] += len(res.records)
                        report["ocr_pages"] += res.ocr_pages
                    else:
                        row.chunk_count = 0
                        row.artifact_sha256 = None  # no artifact: nothing to verify
                        row.quarantine_reason = res.quarantine_reason
                        set_file_stage_status(row, "extract_status", "quarantined")
                        report["quarantined"] += 1
                    await session.commit()  # per-file commit: kill-mid-run resumes
    finally:
        await engine.dispose()
    return report


def add_extract_parser(sub: Any) -> argparse.ArgumentParser:
    """Register `bulk extract` on an argparse subparsers object (tests-after)."""
    parser = sub.add_parser(
        "extract", help="extract catalogued files to zstd artifacts"
    )
    parser.add_argument("--source-dir", default=settings.LIBRARY_SOURCE_DIR)
    parser.add_argument("--db-url", default=settings.DATABASE_URL)
    parser.add_argument("--artifact-dir", default=settings.LIBRARY_ARTIFACT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=settings.LIBRARY_EXTRACT_WORKERS)
    return parser


async def run_extract_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """CLI wiring for `bulk extract` (tests-after; pure logic is TDD'd)."""
    return await run_extract(
        source_dir=args.source_dir,
        db_url=args.db_url,
        artifact_dir=args.artifact_dir,
        limit=args.limit,
        workers=args.workers,
    )
