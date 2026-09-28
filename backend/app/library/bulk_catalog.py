"""Shortlist catalog: sorted walk, SHA-256 dedup, ledger sync, JSON export.

Contract (plan todo 10):

* Walk ``settings.LIBRARY_SOURCE_DIR`` in SORTED POSIX-lexicographic order by
  relative path, so content-dedup winners are deterministic across runs/hosts.
* Dedup is ALWAYS computed over the FULL catalog. ``--limit`` only gates how
  many winner rows are written downstream (post-dedup gating); it never
  changes which path wins a content hash.
* Every discovered file gets exactly one ledger row keyed by relative POSIX
  path (idempotent re-runs update, never duplicate). Winners carry
  ``parse_status`` ``parsed`` (or ``quarantined`` for unparseable names /
  ``failed`` for unreadable files, each with a recorded reason); content
  duplicates are recorded as ``quarantined`` with
  ``quarantine_reason="duplicate-of:<winner rel path>"`` so downstream stages
  (``bulk extract``) naturally skip already-represented content while the
  mapping stays queryable.
* Only winners with usable provenance (``parsed`` + name-quarantined) are
  exported via the todo-7 :func:`export_manifest`; duplicates and unreadable
  files stay in SQLite only. The source tree is read-only throughout.

Single-writer discipline: this module is the only catalog writer and holds
:func:`single_writer` while syncing SQLite + exporting JSON.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.library import bulk_state
from app.library.bulk_state import export_manifest, set_file_stage_status, single_writer
from app.infrastructure.authority.catalog import parse_filename

import app.models  # noqa: F401  (register ledger metadata)
from app.models.base import Base
from app.models.library_import import LibraryImportFile
from app.repositories.library_import import LibraryImportRepository

_HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ScannedFile:
    """One walked file: content identity plus page count (or a recorded error)."""

    rel: str  # POSIX relative path, the ledger key
    sha256: str | None  # None only when the file cannot be read at all
    size: int
    pages: int | None  # None when pages cannot be determined
    error: str | None = None


def walk_sorted(source_dir: str | Path) -> list[str]:
    """List ``*.pdf`` files under ``source_dir`` sorted POSIX-lexicographically."""
    root = Path(source_dir)
    rels = [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() == ".pdf"
    ]
    return sorted(rels)


def hash_file(path: str | Path) -> tuple[str, int]:
    """Stream SHA-256 + byte size. Raises on unreadable files."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def count_pages(path: str | Path) -> int:
    """Return the PDF page count. Raises on corrupt/unreadable files."""
    import fitz

    doc = fitz.open(str(path))
    try:
        return len(doc)
    finally:
        doc.close()


def scan_file(source_dir: str | Path, rel: str) -> ScannedFile:
    """Hash + page-count one file. Never raises: failures are recorded."""
    full = Path(source_dir) / rel
    try:
        sha, size = hash_file(full)
    except OSError as exc:
        return ScannedFile(rel=rel, sha256=None, size=0, pages=None, error=str(exc))
    try:
        pages = count_pages(full)
    except Exception as exc:  # corrupt PDF, MuPDF errors are not all OSError
        return ScannedFile(
            rel=rel, sha256=sha, size=size, pages=None, error=f"page-count: {exc}"
        )
    return ScannedFile(rel=rel, sha256=sha, size=size, pages=pages)


def deduplicate(
    scanned: list[ScannedFile],
) -> tuple[list[ScannedFile], dict[str, str]]:
    """Split a FULL-catalog scan into winners + ``{dup_rel: winner_rel}``.

    Input order decides ties, so callers must pass POSIX-sorted scans (first
    sorted path wins each content hash). Files with no hash (unreadable) never
    dedup together: each stays its own winner for an honest ``failed`` row.
    """
    ordered = sorted(scanned, key=lambda s: s.rel)
    seen: dict[str, str] = {}
    winners: list[ScannedFile] = []
    dup_of: dict[str, str] = {}
    for item in ordered:
        key = item.sha256 if item.sha256 is not None else f"\0unreadable:{item.rel}"
        if key in seen:
            dup_of[item.rel] = seen[key]
        else:
            seen[key] = item.rel
            winners.append(item)
    return winners, dup_of


def apply_limit(winners: list[ScannedFile], limit: int | None) -> list[ScannedFile]:
    """Post-dedup gating for ``--limit``. ``None`` keeps every winner."""
    if limit is None:
        return list(winners)
    if limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
    return list(winners[:limit])


def _folder_of(rel: str) -> str:
    parent = Path(rel).parent.as_posix()
    return "" if parent == "." else parent


async def _upsert_rows(
    session: Any,
    winners: list[ScannedFile],
    dup_of: dict[str, str],
) -> list[LibraryImportFile]:
    """Upsert ledger rows for gated winners + recorded duplicates.

    Returns the ORM rows for the gated winners (for the JSON export).
    """
    dup_rels = set(dup_of)
    exported: list[LibraryImportFile] = []
    planned = [(w, True) for w in winners]
    # Duplicates are always recorded, even when --limit gates winners: the
    # mapping is cheap and keeps the full-catalog dedup queryable.
    for dup_rel in sorted(dup_rels):
        planned.append(
            (
                ScannedFile(rel=dup_rel, sha256=None, size=0, pages=None),
                False,
            )
        )
    # Winners first (sorted), then duplicates (sorted) - deterministic order.
    repo = LibraryImportRepository(session)
    for item, is_winner in planned:
        row = await repo.find_file_by_path(item.rel)
        if is_winner:
            parsed = parse_filename(Path(item.rel).name, _folder_of(item.rel))
            if item.error is not None:
                status, reason = "failed", item.error
            elif parsed.quarantined:
                status = "quarantined"
                reason = parsed.quarantine_reason or "no detectable law type"
            else:
                status, reason = "parsed", None
            fields = {
                "sha256": item.sha256 or "",
                "bytes": item.size,
                "pages": item.pages or 0,
                "category": parsed.category,
                "source": parsed.source,
                "version": parsed.version,
                "edition": parsed.edition,
                "quarantine_reason": reason,
            }
        else:
            status = "quarantined"
            reason = f"duplicate-of:{dup_of[item.rel]}"
            fields = {"quarantine_reason": reason}
        if row is None:
            row = LibraryImportFile(
                path=item.rel,
                sha256=fields.get("sha256", "") or "",
                bytes=fields.get("bytes", 0),
                pages=fields.get("pages", 0),
                category=fields.get("category", ""),
                source=fields.get("source", ""),
                version=fields.get("version", ""),
                edition=fields.get("edition", ""),
                parse_status="pending",
                quarantine_reason=fields.get("quarantine_reason"),
            )
            session.add(row)
            await session.flush()
        else:
            for name in (
                "sha256",
                "bytes",
                "pages",
                "category",
                "source",
                "version",
                "edition",
            ):
                if name in fields and fields[name] not in (None, ""):
                    setattr(row, name, fields[name])
                elif name in fields and name in ("sha256", "bytes", "pages"):
                    setattr(row, name, fields[name])
            if "quarantine_reason" in fields:
                row.quarantine_reason = fields["quarantine_reason"]
        set_file_stage_status(row, "parse_status", status)
        if status == "parsed":
            row.quarantine_reason = None
        if is_winner:
            exported.append(row)
    return exported


async def run_catalog(
    source_dir: str | Path | None = None,
    db_url: str | None = None,
    manifest_path: str | Path | None = None,
    seed_state_path: str | Path | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Catalog the full source tree: scan, dedup, ledger-sync, export."""
    src = (
        Path(source_dir)
        if source_dir is not None
        else Path(settings.LIBRARY_SOURCE_DIR)
    )
    url = db_url if db_url is not None else settings.DATABASE_URL
    manifest = (
        Path(manifest_path)
        if manifest_path is not None
        else bulk_state.DEFAULT_MANIFEST
    )
    seed_state = (
        Path(seed_state_path)
        if seed_state_path is not None
        else bulk_state.DEFAULT_SEED_STATE
    )

    rels = walk_sorted(src)
    scanned = [scan_file(src, rel) for rel in rels]
    winners, dup_of = deduplicate(scanned)  # FULL catalog, before any limit
    gated = apply_limit(winners, limit)

    engine = create_async_engine(url, connect_args={"timeout": 30})
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        with single_writer():
            async with maker() as session:
                exported_rows = await _upsert_rows(session, gated, dup_of)
                await session.commit()
                for row in exported_rows:
                    await session.refresh(row)
                exportable = [r for r in exported_rows if r.parse_status != "failed"]
                summary = export_manifest(exportable, manifest, seed_state)
    finally:
        await engine.dispose()

    failed = sum(1 for w in gated if w.error is not None)
    quarantined = sum(
        1
        for w in gated
        if w.error is None
        and parse_filename(Path(w.rel).name, _folder_of(w.rel)).quarantined
    )
    return {
        "discovered": len(rels),
        "duplicates": len(dup_of),
        "catalogued": len(gated),
        "parsed": len(gated) - failed - quarantined,
        "quarantined": quarantined,
        "failed": failed,
        "manifest_entries": summary["manifest_entries"],
        "state_keys": summary["state_keys"],
    }


def add_catalog_parser(sub: Any) -> argparse.ArgumentParser:
    """Register `bulk catalog` on an argparse subparsers object (tests-after)."""
    parser = sub.add_parser("catalog", help="catalog the shortlist with content dedup")
    parser.add_argument("--source-dir", default=settings.LIBRARY_SOURCE_DIR)
    parser.add_argument("--db-url", default=settings.DATABASE_URL)
    parser.add_argument("--manifest", default=str(bulk_state.DEFAULT_MANIFEST))
    parser.add_argument("--seed-state", default=str(bulk_state.DEFAULT_SEED_STATE))
    parser.add_argument("--limit", type=int, default=None)
    return parser


async def run_catalog_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """CLI wiring for `bulk catalog` (tests-after; pure logic is TDD'd)."""
    return await run_catalog(
        source_dir=args.source_dir,
        db_url=args.db_url,
        manifest_path=args.manifest,
        seed_state_path=args.seed_state,
        limit=args.limit,
    )


def _main_sync(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI
    parser = argparse.ArgumentParser(prog="bulk catalog")
    parser.add_argument("--source-dir", default=settings.LIBRARY_SOURCE_DIR)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    report = asyncio.run(run_catalog(source_dir=args.source_dir, limit=args.limit))
    print(
        f"catalog: discovered={report['discovered']} "
        f"duplicates={report['duplicates']} catalogued={report['catalogued']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI
    raise SystemExit(_main_sync())
