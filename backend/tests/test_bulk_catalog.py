"""Todo 10 acceptance: catalog walk/sort/dedup/limit/ledger-sync (TDD, tiny fixtures).

The real shortlist (~1GB, 912 PDFs) is NEVER touched by these tests: every
case builds a tiny tmp fixture dir (2-4 small PDFs). The live shortlist run
is the manual-QA channel and lands in the evidence JSON, not in pytest.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register metadata)
from app.models.base import Base
from app.models.library_import import LibraryImportFile


def _tiny_pdf(path: Path, text: str = "hello") -> None:
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def src(tmp_path: Path) -> Path:
    root = tmp_path / "shortlist"
    # Winner sorts first ("aaa" < "zzz"); byte-identical copy => dedup keeps aaa.
    _tiny_pdf(root / "cat" / "aaa ظهير شريف رقم 1.pdf", "body-A")
    (root / "cat" / "zzz ظهير شريف رقم 1.pdf").write_bytes(
        (root / "cat" / "aaa ظهير شريف رقم 1.pdf").read_bytes()
    )
    # Unparseable name but unique content => quarantined winner, still catalogued.
    _tiny_pdf(root / "other" / "random-scan.pdf", "body-B")
    # Corrupt bytes with a .pdf suffix => failed row with a reason, never a crash.
    broken = root / "other" / "broken.pdf"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"not a pdf at all")
    # Non-PDF files are out of scope for the catalog walk.
    ignored = root / "other" / "notes.txt"
    ignored.write_text("ignore me")
    return root


def test_walk_sorted_is_posix_lexicographic(src: Path):
    from app.library.bulk_catalog import walk_sorted

    # Given: fixture files created in arbitrary order
    # When: walking the source dir
    rels = walk_sorted(src)
    # Then: POSIX-lexicographic by relative path, PDFs only
    assert rels == sorted(rels)
    assert all(r.endswith(".pdf") for r in rels)
    assert "other/notes.txt" not in rels
    assert len(rels) == 4


def test_dedup_first_sorted_path_wins(src: Path):
    from app.library.bulk_catalog import deduplicate, scan_file, walk_sorted

    # Given: two files with identical bytes, "aaa" sorts before "zzz"
    scanned = [scan_file(src, rel) for rel in walk_sorted(src)]
    assert all(s.error is None for s in scanned if "broken" not in s.rel)
    # When: deduplicating the FULL scan
    winners, dup_of = deduplicate(scanned)
    # Then: aaa wins, zzz is recorded as its duplicate
    winner_rels = [w.rel for w in winners]
    assert "cat/aaa ظهير شريف رقم 1.pdf" in winner_rels
    assert "cat/zzz ظهير شريف رقم 1.pdf" not in winner_rels
    assert dup_of["cat/zzz ظهير شريف رقم 1.pdf"] == "cat/aaa ظهير شريف رقم 1.pdf"


def test_limit_never_changes_dedup_winners(src: Path):
    from app.library.bulk_catalog import (
        apply_limit,
        deduplicate,
        scan_file,
        walk_sorted,
    )

    # Given: a full-catalog dedup result
    scanned = [scan_file(src, rel) for rel in walk_sorted(src)]
    winners, dup_of = deduplicate(scanned)
    full_winner = [w.rel for w in winners]
    # When: gating with --limit
    gated = apply_limit(winners, 1)
    # Then: the gated set is a prefix of the full winners; dup mapping untouched
    assert [w.rel for w in gated] == full_winner[:1]
    assert dup_of["cat/zzz ظهير شريف رقم 1.pdf"] == "cat/aaa ظهير شريف رقم 1.pdf"


def test_scan_file_records_unreadable_instead_of_raising(src: Path):
    from app.library.bulk_catalog import scan_file

    # Given: a corrupt PDF
    # When: scanning it
    rec = scan_file(src, "other/broken.pdf")
    # Then: recorded with a reason, never raised; hash still identifies content
    assert rec.error is not None
    assert rec.sha256 is not None and len(rec.sha256) == 64
    assert rec.pages is None


async def _run(src: Path, tmp_path: Path, **kw):
    from app.library.bulk_catalog import run_catalog

    db = tmp_path / kw.pop("db_name", "cat.db")
    manifest = tmp_path / "library-manifest.json"
    seed = tmp_path / "library-seed-state.json"
    report = await run_catalog(
        source_dir=src,
        db_url=f"sqlite+aiosqlite:///{db}",
        manifest_path=manifest,
        seed_state_path=seed,
        **kw,
    )
    return report, db, manifest, seed


async def test_run_catalog_writes_ledger_and_exports(src: Path, tmp_path: Path):
    # Given: the tiny fixture source
    # When: running the catalog
    report, db, manifest, seed = await _run(src, tmp_path)
    # Then: discovered/duplicates/catalogued add up; JSON exported
    assert report["discovered"] == 4
    assert report["duplicates"] == 1
    assert report["catalogued"] == 3
    assert manifest.exists() and seed.exists()
    # And: ledger holds one row per discovered file (winners + recorded dups)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        rows = (await s.execute(sa.select(LibraryImportFile))).scalars().all()
        by_path = {r.path: r for r in rows}
    await engine.dispose()
    assert len(rows) == 4
    dup = by_path["cat/zzz ظهير شريف رقم 1.pdf"]
    assert dup.parse_status == "quarantined"
    assert dup.quarantine_reason == "duplicate-of:cat/aaa ظهير شريف رقم 1.pdf"
    assert by_path["other/random-scan.pdf"].parse_status == "quarantined"
    assert by_path["other/broken.pdf"].parse_status == "failed"
    assert by_path["other/broken.pdf"].quarantine_reason is not None


async def test_run_catalog_is_idempotent(src: Path, tmp_path: Path):
    # Given: one completed catalog run
    first, db, _, _ = await _run(src, tmp_path)
    # When: running it again unchanged
    second, _, _, _ = await _run(src, tmp_path, db_name=db.name)
    # Then: identical counts and no extra rows
    assert second["discovered"] == first["discovered"] == 4
    assert second["duplicates"] == first["duplicates"] == 1
    assert second["catalogued"] == first["catalogued"] == 3
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        n = (await s.execute(sa.func.count(LibraryImportFile.id))).scalar_one()
    await engine.dispose()
    assert n == 4


async def test_run_catalog_limit_gates_writes_not_winners(src: Path, tmp_path: Path):
    # Given: the tiny fixture source
    # When: cataloguing with --limit 1
    report, db, _, _ = await _run(src, tmp_path, limit=1)
    # Then: dedup still sees the duplicate, but only one winner row is written
    assert report["discovered"] == 4
    assert report["duplicates"] == 1
    assert report["catalogued"] == 1
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        n = (await s.execute(sa.func.count(LibraryImportFile.id))).scalar_one()
    await engine.dispose()
    # 1 winner + 1 recorded duplicate
    assert n == 2


def test_bulk_cli_dispatches_catalog(src: Path, tmp_path: Path, monkeypatch):
    import app.library.bulk as bulk

    # Given: the catalog entry point wired through the dispatcher
    out: dict = {}

    async def fake_run(args):
        out.update(vars(args))
        return {"discovered": 0, "duplicates": 0, "catalogued": 0}

    monkeypatch.setattr(bulk, "_run_catalog", fake_run)
    # When: dispatching `bulk catalog` with a limit
    rc = bulk.main(
        [
            "catalog",
            "--source-dir",
            str(src),
            "--db-url",
            "sqlite+aiosqlite:///:memory:",
            "--limit",
            "5",
        ]
    )
    # Then: the limit reaches the runner unchanged and exit is 0
    assert rc == 0
    assert out["limit"] == 5
    assert Path(out["source_dir"]) == src
