"""Todo 7 acceptance: ledger status machine, atomic export, writer discipline, runs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register metadata)
from app.library import bulk_state
from app.library.bulk_state import (
    InvalidTransitionError,
    WriterBusyError,
    atomic_write_json,
    bump_indexed_count,
    export_manifest,
    set_chunk_status,
    set_file_stage_status,
    single_writer,
    validate_run_kind,
)
from app.models.base import Base
from app.models.library_import import (
    LibraryImportChunk,
    LibraryImportFile,
    LibraryImportRun,
)


def _file(**kw) -> LibraryImportFile:
    base = {
        "path": "/docs/a.pdf",
        "sha256": "0" * 64,
        "source": "Dahir",
        "version": "1.11.151",
        "edition": "ar-general",
    }
    base.update(kw)
    return LibraryImportFile(**base)


@pytest.fixture()
async def db(tmp_path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/bulk.db",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def test_file_stage_transitions_are_idempotent_and_terminal():
    # Given: a freshly catalogued file row
    row = _file()
    # When: advancing parse -> parsed twice
    set_file_stage_status(row, "parse_status", "parsed")
    set_file_stage_status(row, "parse_status", "parsed")
    # Then: stable at parsed (idempotent re-run)
    assert row.parse_status == "parsed"
    # When/Then: regression from terminal DONE is refused
    with pytest.raises(InvalidTransitionError):
        set_file_stage_status(row, "parse_status", "pending")
    # When/Then: unknown value is refused
    with pytest.raises(InvalidTransitionError):
        set_file_stage_status(row, "extract_status", "zapped")
    # Given: a quarantined extract can retry
    row2 = _file(path="/docs/b.pdf", extract_status="quarantined")
    set_file_stage_status(row2, "extract_status", "pending")
    set_file_stage_status(row2, "extract_status", "extracted")
    assert row2.extract_status == "extracted"
    # Given: a failed embed can complete directly on retry
    row3 = _file(path="/docs/c.pdf", embed_status="failed")
    set_file_stage_status(row3, "embed_status", "embedded")
    assert row3.embed_status == "embedded"


def test_chunk_status_transitions_are_idempotent_and_terminal():
    # Given: a pending chunk
    row = LibraryImportChunk(chunk_id="c1", file_id=1, ord=0)
    # When: indexed twice
    set_chunk_status(row, "indexed")
    set_chunk_status(row, "indexed")
    # Then: stable + regression refused
    assert row.status == "indexed"
    with pytest.raises(InvalidTransitionError):
        set_chunk_status(row, "pending")


def test_reexport_is_idempotent(tmp_path: Path):
    # Given: two ledger rows exported once
    rows = [
        _file(path="/docs/b.pdf", source="Z"),
        _file(path="/docs/a.pdf", source="A", chunk_count=3, indexed_count=2),
    ]
    manifest = tmp_path / "library-manifest.json"
    seed = tmp_path / "library-seed-state.json"
    # When: exported twice
    out1 = export_manifest(rows, manifest, seed)
    first_manifest = manifest.read_bytes()
    first_seed = seed.read_bytes()
    manifest_mtime = manifest.stat().st_mtime_ns
    seed_mtime = seed.stat().st_mtime_ns
    out2 = export_manifest(rows, manifest, seed)
    # Then: first export wrote, second skipped the rewrite (no wholesale
    # rewrite); files stay byte- AND mtime-identical
    assert out1["manifest_entries"] == out2["manifest_entries"] == 2
    assert out1["state_keys"] == out2["state_keys"] == 2
    assert out1["rewrote_manifest"] is True and out1["rewrote_seed_state"] is True
    assert out2["rewrote_manifest"] is False and out2["rewrote_seed_state"] is False
    assert manifest.read_bytes() == first_manifest
    assert seed.read_bytes() == first_seed
    assert manifest.stat().st_mtime_ns == manifest_mtime
    assert seed.stat().st_mtime_ns == seed_mtime
    payload = json.loads(first_manifest)
    assert [e["source"] for e in payload["entries"]] == ["A", "Z"]
    state = json.loads(first_seed)
    assert state["A@1.11.151#ar-general"] == {"chunks": 3, "embedded": 2}


def test_atomic_write_survives_crash_mid_write(tmp_path: Path, monkeypatch):
    # Given: a previously exported manifest on disk
    dest = tmp_path / "library-manifest.json"
    atomic_write_json(dest, {"entries": []})
    before = dest.read_bytes()
    # When: serialization crashes mid-export
    monkeypatch.setattr(
        json, "dumps", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    with pytest.raises(OSError, match="boom"):
        atomic_write_json(dest, {"entries": [{"x": 1}]})
    # Then: the previous file is byte-identical, no partial JSON, no stray tmp
    assert dest.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_second_writer_is_rejected(tmp_path: Path):
    # Given: one held writer lock
    lock = tmp_path / ".library-writer.lock"
    with single_writer(lock):
        # When/Then: a second writer is refused, never queued behind the first
        with pytest.raises(WriterBusyError):
            with single_writer(lock):
                pass


async def test_no_lost_update_under_two_writers(db):
    # Given: one ledger file row
    async with db() as s:
        s.add(_file(chunk_count=100))
        await s.commit()
        file_id = (await s.execute(sa.select(LibraryImportFile.id))).scalar_one()
    # When: two concurrent writers each bump 20 times
    N = 20

    async def writer():
        async with db() as s:
            for _ in range(N):
                await bump_indexed_count(s, file_id, 1)
                await s.commit()

    await asyncio.gather(writer(), writer())
    # Then: exact count, nothing lost to read-modify-write races
    async with db() as s:
        row = (await s.execute(sa.select(LibraryImportFile))).scalar_one()
        assert row.indexed_count == 2 * N


@pytest.mark.parametrize("kind", ["migrate", "reembed-matter"])
async def test_import_runs_roundtrip_per_kind(db, kind: str):
    # Given: a validated kind
    assert validate_run_kind(kind) == kind
    # When: a run row is written and read back
    async with db() as s:
        s.add(
            LibraryImportRun(
                kind=kind,
                model="granite-embedding-107m-multilingual",
                dim=384,
                git_sha="a" * 40,
                status="succeeded",
                payload_json={"counts": {"indexed": 7}},
            )
        )
        await s.commit()
    async with db() as s:
        rows = (await s.execute(sa.select(LibraryImportRun))).scalars().all()
    # Then: exact row content survives the round trip
    assert len(rows) == 1
    assert (rows[0].kind, rows[0].model, rows[0].dim) == (
        kind,
        "granite-embedding-107m-multilingual",
        384,
    )
    assert rows[0].payload_json == {"counts": {"indexed": 7}}


async def test_import_runs_reject_unknown_kind(db):
    # Given/When/Then: an unknown kind is rejected at the boundary ...
    with pytest.raises(ValueError, match="invalid library_import_runs kind"):
        validate_run_kind("reindex-everything")
    # ... and at the DB CHECK constraint when bypassing the helper
    async with db() as s:
        s.add(
            LibraryImportRun(
                kind="reindex-everything", model="m", dim=1, git_sha="b" * 40
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()


def test_bulk_state_module_single_writer_discipline_documented():
    # The ownership contract must stay visible in the module docstring.
    doc = bulk_state.__doc__
    assert doc is not None
    assert "SOURCE OF TRUTH" in doc
    assert "export_manifest" in doc
    assert "single_writer" in doc
