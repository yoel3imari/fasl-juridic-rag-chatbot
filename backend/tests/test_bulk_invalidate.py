"""Acceptance for `bulk invalidate`: derived state is discarded, provenance is not.

No corpus access, no embedding calls, no Qdrant: tmp dirs + tiny SQLite
ledgers only. The invariant under test is the one that made the served-budget
fix inert if skipped -- after a pipeline change every artifact, embed cache
and chunk row is stale (chunk ids hash the chunker's own output), while the
catalog provenance stays authoritative.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register metadata)
from app.cli.library.artifacts import build_record, write_artifact
from app.cli.library.bulk_invalidate import (
    InvalidationRefused,
    invalidate,
    select_invalidation_targets,
)
from app.cli.library.bulk_run import EMBED_CACHE_SUFFIX
from app.models.base import Base
from app.models.library_import import LibraryImportChunk, LibraryImportFile


def _rec(i: int, file_sha: str) -> dict:
    return build_record(
        chunk_id=f"id-{i}",
        text=f"text-{i} المادة",
        page=i + 1,
        hierarchy={},
        article="art",
        tokens=i,
        source="s",
        version="v",
        edition="e",
        category="c",
        file_sha=file_sha,
    )


def _seeded_engine(tmp_path: Path, rows: list[LibraryImportFile]):
    url = f"sqlite+aiosqlite:///{tmp_path / 'l.db'}"
    engine = create_async_engine(url, connect_args={"timeout": 30})

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            for row in rows:
                s.add(row)
            await s.commit()
        return maker

    return engine, _seed


async def _seed_rows(s, file_id: int, n: int = 2) -> None:
    for i in range(n):
        s.add(LibraryImportChunk(chunk_id=f"id-{i}", file_id=file_id, ord=i, status="indexed"))
    await s.commit()


def _done_row(**kw) -> LibraryImportFile:
    base = {
        "path": "cat/a.pdf",
        "sha256": "a" * 64,
        "bytes": 10,
        "pages": 2,
        "category": "المادة المدنية",
        "source": "ظهير شريف",
        "version": "1.11.151",
        "edition": "ar-general",
        "parse_status": "parsed",
        "extract_status": "extracted",
        "embed_status": "embedded",
        "index_status": "indexed",
        "chunk_count": 2,
        "indexed_count": 2,
        "artifact_sha256": "b" * 64,
    }
    base.update(kw)
    return LibraryImportFile(**base)


# --- pure selection ---------------------------------------------------------


def test_selection_targets_only_files_with_derived_state() -> None:
    # Duck-typed stubs: the selector is pure and reads only these two
    # attributes, so it is unit-tested without an engine (persisted rows would
    # be the integration test, which test_invalidate_discards_... already is).
    def stub(fid: int, *, sha: str | None, extract: str) -> SimpleNamespace:
        return SimpleNamespace(id=fid, artifact_sha256=sha, extract_status=extract)

    done = stub(1, sha="b" * 64, extract="extracted")
    # Extracted but the sha was never recorded (pre-todo-13 row): still a
    # target -- there IS a derived artifact to discard.
    no_sha = stub(2, sha=None, extract="extracted")
    # Never produced an artifact: nothing derived, must be left alone.
    dup = stub(3, sha=None, extract="pending")
    assert select_invalidation_targets([done, no_sha, dup]) == [1, 2]


# --- refusal ----------------------------------------------------------------


def test_invalidate_refuses_without_confirm(tmp_path: Path) -> None:
    import asyncio

    with pytest.raises(InvalidationRefused, match="--confirm"):
        asyncio.run(
            invalidate(
                f"sqlite+aiosqlite:///{tmp_path / 'l.db'}",
                artifact_dir=tmp_path / "art",
                confirm=False,
            )
        )


# --- the real reset ---------------------------------------------------------


def test_invalidate_discards_derived_state_and_keeps_provenance(tmp_path: Path) -> None:
    import asyncio

    adir = tmp_path / "art"
    sha = "a" * 64
    artifact = adir / f"{sha}.jsonl.zst"
    cache = adir / f"{sha}{EMBED_CACHE_SUFFIX}"
    write_artifact(iter([_rec(0, sha), _rec(1, sha)]), artifact)
    write_artifact(iter([{"chunk_id": "id-0", "vector": [0.1] * 384}]), cache)
    assert artifact.exists() and cache.exists()

    row = _done_row(sha256=sha)
    engine, seed = _seeded_engine(tmp_path, [row])
    maker = asyncio.run(seed())

    async def _add_chunks():
        async with maker() as s:
            await _seed_rows(s, row.id)

    asyncio.run(_add_chunks())

    report = asyncio.run(
        invalidate(
            f"sqlite+aiosqlite:///{tmp_path / 'l.db'}",
            artifact_dir=adir,
            confirm=True,
        )
    )
    assert report["files_reset"] == 1
    assert report["chunks_deleted"] == 2
    assert report["artifacts_removed"] == 1
    # The embed cache MUST go: ensure_embed seeds from disk and writes the
    # MERGED dict back, so a surviving cache is re-persisted forever.
    assert report["caches_removed"] == 1
    assert not artifact.exists()
    assert not cache.exists()

    async def _check():
        async with maker() as s:
            got = (await s.execute(sa.select(LibraryImportFile))).scalars().one()
            # derived state is gone ...
            assert got.extract_status == "pending"
            assert got.embed_status == "pending"
            assert got.index_status == "pending"
            assert got.chunk_count == 0
            assert got.indexed_count == 0
            assert got.artifact_sha256 is None
            assert (await s.execute(sa.select(LibraryImportChunk))).scalars().all() == []
            # ... authoritative provenance survives untouched
            assert got.path == "cat/a.pdf"
            assert got.sha256 == sha
            assert got.source == "ظهير شريف"
            assert got.category == "المادة المدنية"
            assert got.parse_status == "parsed"
            assert got.quarantine_reason is None

    asyncio.run(_check())
    asyncio.run(engine.dispose())


def test_invalidate_writes_a_fingerprint_sentinel(tmp_path: Path) -> None:
    import asyncio

    row = _done_row(path="cat/a.pdf")
    engine, seed = _seeded_engine(tmp_path, [row])
    asyncio.run(seed())
    report = asyncio.run(
        invalidate(
            f"sqlite+aiosqlite:///{tmp_path / 'l.db'}",
            artifact_dir=tmp_path / "art",
            confirm=True,
        )
    )
    # The ledger must record which fingerprint the NEXT artifacts belong to,
    # so a stale-artifact diagnosis is answerable after the reset.
    assert len(report["pipeline_fingerprint"]) == 40
    asyncio.run(engine.dispose())
