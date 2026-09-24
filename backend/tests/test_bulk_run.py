"""Todo 16: end-to-end orchestration (embed + index, resumable).

TDD refusal-first: the preflight MUST refuse (non-zero, before any
embed/index call) on stale/missing/failed gates and on missing/stale
migrate + reembed-matter sentinels. Killer inputs are pre-filtered and
never sent. Reconciliation failure marks ONLY verified ids. Resume is a
no-op for finished work. All tests use tmp DBs + fake embedders/stores --
never the live :6333 slate or the live :8080 service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register ledger metadata)
from app.library import bulk_run
from app.library.artifacts import build_record, sha256_file, write_artifact
from app.library.bulk_migrate import live_git_sha
from app.library.bulk_run import (
    FAIL_EXIT,
    RunFailed,
    RunRefused,
    check_preflight,
    ensure_embed,
    ensure_index,
    run_embed,
    scope_file_ids,
)
from app.models.base import Base
from app.models.library_import import (
    LibraryImportChunk,
    LibraryImportFile,
    LibraryImportRun,
)
from app.search.store import IndexBatchResult, IndexReconciliationError

MODEL = "granite-embedding-107m"
DIM = 4  # tiny test dim (live is 384; dim flows through as a parameter)
SHA = live_git_sha()


def _good_gate() -> dict:
    return {
        "model": MODEL,
        "dim": DIM,
        "git_sha": SHA,
        "pass": True,
        "recall_at_10_granite": 0.95,
        "recall_at_10_bge": 1.0,
    }


def _good_parity() -> dict:
    return {"model": MODEL, "dim": DIM, "git_sha": SHA, "mean_cosine": 0.993}


def _good_sentinel(i: int = 1) -> dict:
    return {
        "id": i,
        "kind": "migrate",
        "model": MODEL,
        "dim": DIM,
        "git_sha": SHA,
        "status": "done",
    }


def _preflight_ok() -> dict:
    return bulk_run.preflight(
        _good_gate(),
        _good_parity(),
        _good_sentinel(1),
        {**_good_sentinel(2), "kind": "reembed-matter"},
        model=MODEL,
        dim=DIM,
        sha=SHA,
    )


# --- pure preflight ------------------------------------------------------------


def test_preflight_ok_reports_sentinel_ids() -> None:
    out = _preflight_ok()
    assert out["migrate_id"] == 1
    assert out["reembed_matter_id"] == 2
    assert out["parity_mean_cosine"] == pytest.approx(0.993)


def test_preflight_stale_gate_refuses() -> None:
    gate = _good_gate()
    gate["git_sha"] = "deadbeef"
    with pytest.raises(RunRefused, match="stale"):
        bulk_run.preflight(
            gate,
            _good_parity(),
            _good_sentinel(),
            {**_good_sentinel(), "kind": "reembed-matter"},
            model=MODEL,
            dim=DIM,
            sha=SHA,
        )


def test_preflight_failed_gate_refuses() -> None:
    gate = _good_gate()
    gate["pass"] = False
    with pytest.raises(RunRefused, match="pass=False"):
        bulk_run.preflight(
            gate,
            _good_parity(),
            _good_sentinel(),
            _good_sentinel(),
            model=MODEL,
            dim=DIM,
            sha=SHA,
        )


def test_preflight_low_parity_refuses() -> None:
    parity = _good_parity()
    parity["mean_cosine"] = 0.97
    with pytest.raises(RunRefused, match="mean_cosine"):
        bulk_run.preflight(
            _good_gate(),
            parity,
            _good_sentinel(),
            _good_sentinel(),
            model=MODEL,
            dim=DIM,
            sha=SHA,
        )


def test_preflight_missing_migrate_sentinel_refuses() -> None:
    with pytest.raises(RunRefused, match="kind='migrate'"):
        bulk_run.preflight(
            _good_gate(),
            _good_parity(),
            None,
            _good_sentinel(),
            model=MODEL,
            dim=DIM,
            sha=SHA,
        )


def test_preflight_missing_reembed_sentinel_refuses() -> None:
    with pytest.raises(RunRefused, match="kind='reembed-matter'"):
        bulk_run.preflight(
            _good_gate(),
            _good_parity(),
            _good_sentinel(),
            None,
            model=MODEL,
            dim=DIM,
            sha=SHA,
        )


def test_preflight_stale_sentinel_refuses() -> None:
    row = _good_sentinel()
    row["git_sha"] = "5b65a0c"
    with pytest.raises(RunRefused, match="stale"):
        bulk_run.preflight(
            _good_gate(),
            _good_parity(),
            row,
            _good_sentinel(),
            model=MODEL,
            dim=DIM,
            sha=SHA,
        )


def test_missing_gate_file_refuses() -> None:
    with pytest.raises(RunRefused, match="missing"):
        bulk_run.load_gate_artifact("/tmp/does-not-exist-xyz.json", "quality_gate")


# --- tmp-DB fixtures ------------------------------------------------------------


def _db_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/t16.db"


async def _mkdb(db_url: str):
    engine = create_async_engine(db_url, connect_args={"timeout": 30})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine


def _rec(i: int, file_sha: str, text: str = "نص المادة") -> dict:
    return build_record(
        chunk_id=f"cid-{i}",
        text=f"{text} {i}",
        page=i + 1,
        hierarchy={"code": "c"},
        article=f"art-{i}",
        tokens=10,
        source="s",
        version="v",
        edition="e",
        category="c",
        file_sha=file_sha,
    )


async def _seed_file(session, tmp_path: Path, name: str, n: int = 3) -> Any:
    """Seed one extracted+verifiable file with a real artifact on disk."""
    adir = tmp_path / "art"
    adir.mkdir(exist_ok=True)
    import hashlib as _hl

    file_sha = _hl.sha256(name.encode()).hexdigest()
    dest = adir / f"{file_sha}.jsonl.zst"
    write_artifact(iter([_rec(i, file_sha) for i in range(n)]), dest)
    row = LibraryImportFile(
        path=name,
        sha256=file_sha,
        source="s",
        version="v",
        edition="e",
        parse_status="parsed",
        extract_status="extracted",
        embed_status="pending",
        index_status="pending",
        chunk_count=n,
        artifact_sha256=sha256_file(dest),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _seed_sentinels(session) -> None:
    session.add(
        LibraryImportRun(
            kind="migrate",
            model=MODEL,
            dim=DIM,
            git_sha=SHA,
            status="done",
            payload_json={},
        )
    )
    session.add(
        LibraryImportRun(
            kind="reembed-matter",
            model=MODEL,
            dim=DIM,
            git_sha=SHA,
            status="done",
            payload_json={},
        )
    )
    await session.commit()


def _args(tmp_path: Path, **over) -> argparse.Namespace:
    base = {
        "source_dir": str(tmp_path / "src"),
        "db_url": _db_url(tmp_path),
        "artifact_dir": str(tmp_path / "art"),
        "limit": None,
        "qdrant_url": None,
        "qdrant_local_path": None,
        "embed_url": "http://127.0.0.1:9",
        "gate": str(tmp_path / "gate.json"),
        "parity": str(tmp_path / "parity.json"),
    }
    base.update(over)
    return argparse.Namespace(**base)


def _write_gates(
    tmp_path: Path, gate: dict | None = None, parity: dict | None = None
) -> None:
    (tmp_path / "gate.json").write_text(
        json.dumps(gate or _good_gate()), encoding="utf-8"
    )
    (tmp_path / "parity.json").write_text(
        json.dumps(parity or _good_parity()), encoding="utf-8"
    )


class _FakeEmbedder:
    """Counts calls; asserts killer inputs are never sent."""

    def __init__(self, dim: int = DIM, forbid: tuple[str, ...] = ()) -> None:
        self.dim = dim
        self.forbid = forbid
        self.calls: list[list[str]] = []

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        for t in texts:
            assert all(f not in t for f in self.forbid), f"killer input sent: {t!r}"
        self.calls.append(list(texts))
        return [[0.1] * self.dim for _ in texts]


class _FakeStore:
    def __init__(self, drop: set[str] | None = None) -> None:
        self.points: dict[str, dict] = {}
        self.drop = drop or set()

    def count_points(self, collection: str) -> int:
        return len(self.points)

    def index_authorities_batched(self, points) -> IndexBatchResult:
        pts = list(points)
        got = [p for p in pts if p["id"] not in self.drop]
        for p in got:
            self.points[p["id"]] = p
        if len(got) != len(pts):
            missing = [p["id"] for p in pts if p["id"] in self.drop]
            raise IndexReconciliationError(
                f"reconciliation failed: {len(missing)}/{len(pts)} missing",
                verified_ids=[p["id"] for p in got],
                missing_ids=missing,
                expected=len(pts),
                actual=len(got),
            )
        return IndexBatchResult(
            verified_ids=[p["id"] for p in pts],
            expected=len(pts),
            count_before=0,
            count_after=len(self.points),
            batches=1,
            spot_checked=min(5, len(pts)),
        )


# --- refusal BEFORE any embed/index work ----------------------------------------


def test_run_embed_stale_gate_refuses_before_any_embed_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a stale gate (sha mismatch) + an extracted file ready to embed
    gate = _good_gate()
    gate["git_sha"] = "stale-sha"
    _write_gates(tmp_path, gate=gate)
    engine = asyncio.run(_mkdb(_db_url(tmp_path)))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> None:
        async with maker() as s:
            await _seed_file(s, tmp_path, "cat/a.pdf")
            await _seed_sentinels(s)

    asyncio.run(_seed())
    fake = _FakeEmbedder()
    monkeypatch.setattr(bulk_run, "_make_embedder", lambda *a, **k: fake)
    # When: bulk embed runs (via the CLI wrapper: refusal -> SystemExit(2))
    # Then: SystemExit(2) and the embedder was NEVER called
    with pytest.raises(SystemExit) as ei:
        asyncio.run(bulk_run.run_embed_from_args(_args(tmp_path)))
    assert ei.value.code == FAIL_EXIT
    assert fake.calls == []
    asyncio.run(engine.dispose())


def test_check_preflight_missing_migrate_row_refuses(tmp_path: Path) -> None:
    engine = asyncio.run(_mkdb(_db_url(tmp_path)))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _go():
        async with maker() as s:
            s.add(
                LibraryImportRun(
                    kind="reembed-matter",
                    model=MODEL,
                    dim=DIM,
                    git_sha=SHA,
                    status="done",
                    payload_json={},
                )
            )
            await s.commit()
            with pytest.raises(RunRefused, match="kind='migrate'"):
                await check_preflight(
                    s, gate_path="x", parity_path="y", model=MODEL, dim=DIM, sha=SHA
                )

    # gate load happens first; write good gates then delete sentinel path:
    _write_gates(tmp_path)

    async def _go2():
        async with maker() as s:
            s.add(
                LibraryImportRun(
                    kind="reembed-matter",
                    model=MODEL,
                    dim=DIM,
                    git_sha=SHA,
                    status="done",
                    payload_json={},
                )
            )
            await s.commit()
            with pytest.raises(RunRefused, match="kind='migrate'"):
                await check_preflight(
                    s,
                    gate_path=str(tmp_path / "gate.json"),
                    parity_path=str(tmp_path / "parity.json"),
                    model=MODEL,
                    dim=DIM,
                    sha=SHA,
                )

    asyncio.run(_go2())
    asyncio.run(engine.dispose())


# --- killer-input pre-filter -------------------------------------------------------


def test_embed_prefilter_never_sends_overlong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: served_estimate flags chunk 1 as overlong
    monkeypatch.setattr(
        bulk_run, "_served_estimate", lambda t: 999 if "KILLER" in t else 10
    )
    monkeypatch.setattr(bulk_run, "_served_safe_tokens", lambda: 500)
    engine = asyncio.run(_mkdb(_db_url(tmp_path)))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> int:
        async with maker() as s:
            row = await _seed_file(s, tmp_path, "cat/a.pdf", n=2)
            # rewrite artifact: chunk 1 carries the killer text
            adir = tmp_path / "art"
            from app.library.artifacts import artifact_path_for, write_artifact

            recs = [_rec(0, row.sha256), _rec(1, row.sha256, text="KILLER " * 50)]
            write_artifact(iter(recs), artifact_path_for(adir, row.sha256))
            from app.library.artifacts import sha256_file as _sha

            row.artifact_sha256 = _sha(artifact_path_for(adir, row.sha256))
            await s.commit()
            return int(row.id)

    fid = asyncio.run(_seed())
    fake = _FakeEmbedder(forbid=("KILLER",))

    async def _go() -> dict:
        async with maker() as s:
            return await ensure_embed(
                s, [fid], artifact_dir=str(tmp_path / "art"), embedder=fake, dim=DIM
            )

    report = asyncio.run(_go())
    # Then: killer chunk skipped with reason, good chunk embedded, 1 call
    assert report["chunks_skipped"] == 1
    assert report["chunks_embedded"] == 1
    assert len(fake.calls) == 1
    assert all("KILLER" not in t for batch in fake.calls for t in batch)

    async def _check() -> None:
        async with maker() as s:
            rows = (
                (
                    await s.execute(
                        sa.select(LibraryImportChunk).order_by(LibraryImportChunk.ord)
                    )
                )
                .scalars()
                .all()
            )
            assert [c.status for c in rows] == ["embedded", "failed"]

    asyncio.run(_check())
    asyncio.run(engine.dispose())


# --- reconciliation: only verified ids ----------------------------------------------


def test_index_marks_only_verified_ids_on_reconciliation_error(
    tmp_path: Path,
) -> None:
    engine = asyncio.run(_mkdb(_db_url(tmp_path)))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> int:
        async with maker() as s:
            row = await _seed_file(s, tmp_path, "cat/a.pdf", n=3)
            for i in range(3):
                s.add(
                    LibraryImportChunk(
                        chunk_id=f"cid-{i}", file_id=row.id, ord=i, status="embedded"
                    )
                )
            from app.library.bulk_run import write_embed_cache

            await s.commit()
            write_embed_cache(
                tmp_path / "art" / f"{row.sha256}.embed.jsonl.zst",
                {f"cid-{i}": [0.1] * DIM for i in range(3)},
            )
            row.embed_status = "embedded"
            await s.commit()
            return int(row.id)

    fid = asyncio.run(_seed())
    store = _FakeStore(drop={"cid-2"})  # one lost write

    async def _go() -> None:
        async with maker() as s:
            with pytest.raises(RunFailed, match="reconciliation failed"):
                await ensure_index(
                    s, [fid], artifact_dir=str(tmp_path / "art"), store=store
                )

    asyncio.run(_go())

    async def _check() -> None:
        async with maker() as s:
            rows = (
                (
                    await s.execute(
                        sa.select(LibraryImportChunk).order_by(LibraryImportChunk.ord)
                    )
                )
                .scalars()
                .all()
            )
            # Then: ONLY verified ids indexed; the lost one stays pending
            assert [c.status for c in rows] == ["indexed", "indexed", "embedded"]
            f = await s.get(LibraryImportFile, fid)
            assert f.index_status == "pending"  # file NOT claimed indexed

    asyncio.run(_check())
    asyncio.run(engine.dispose())


# --- resume: finished work is never re-driven ------------------------------------------


def test_second_run_is_noop_no_reembed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bulk_run, "_served_estimate", lambda t: 10)
    monkeypatch.setattr(bulk_run, "_served_safe_tokens", lambda: 500)
    engine = asyncio.run(_mkdb(_db_url(tmp_path)))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> int:
        async with maker() as s:
            row = await _seed_file(s, tmp_path, "cat/a.pdf", n=2)
            return int(row.id)

    fid = asyncio.run(_seed())
    fake = _FakeEmbedder()
    store = _FakeStore()

    async def _once() -> tuple[dict, dict]:
        async with maker() as s:
            e = await ensure_embed(
                s, [fid], artifact_dir=str(tmp_path / "art"), embedder=fake, dim=DIM
            )
            i = await ensure_index(
                s, [fid], artifact_dir=str(tmp_path / "art"), store=store
            )
            return e, i

    e1, i1 = asyncio.run(_once())
    assert e1["chunks_embedded"] == 2
    assert i1["chunks_indexed"] == 2
    n_calls = len(fake.calls)
    # When: the same scope runs again (resume)
    e2, i2 = asyncio.run(_once())
    # Then: zero re-embed, zero re-index
    assert len(fake.calls) == n_calls
    assert e2["chunks_embedded"] == 0
    assert i2["chunks_indexed"] == 0
    asyncio.run(engine.dispose())


def test_embed_cache_resume_avoids_server_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill between cache-write and ledger-commit: resume reuses the cache."""
    monkeypatch.setattr(bulk_run, "_served_estimate", lambda t: 10)
    monkeypatch.setattr(bulk_run, "_served_safe_tokens", lambda: 500)
    engine = asyncio.run(_mkdb(_db_url(tmp_path)))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> int:
        async with maker() as s:
            row = await _seed_file(s, tmp_path, "cat/a.pdf", n=2)
            return int(row.id)

    fid = asyncio.run(_seed())

    async def _embed() -> None:
        async with maker() as s:
            await ensure_embed(
                s,
                [fid],
                artifact_dir=str(tmp_path / "art"),
                embedder=_FakeEmbedder(),
                dim=DIM,
            )

    asyncio.run(_embed())

    # Simulate a kill that rolled back the ledger commit but kept the cache:
    # (cache file exists from the run above; reset rows to pending)
    async def _rollback_ledger() -> None:
        async with maker() as s:
            for c in (await s.execute(sa.select(LibraryImportChunk))).scalars():
                c.status = "pending"
            f = await s.get(LibraryImportFile, fid)
            f.embed_status = "pending"
            await s.commit()

    asyncio.run(_rollback_ledger())
    fake2 = _FakeEmbedder()

    async def _resume() -> dict:
        async with maker() as s:
            return await ensure_embed(
                s, [fid], artifact_dir=str(tmp_path / "art"), embedder=fake2, dim=DIM
            )

    report = asyncio.run(_resume())
    assert fake2.calls == []  # zero re-embed: cache served every chunk
    assert report["chunks_reused_cache"] == 2
    asyncio.run(engine.dispose())


def test_scope_is_deterministic_path_order(tmp_path: Path) -> None:
    engine = asyncio.run(_mkdb(_db_url(tmp_path)))
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _go() -> list[int]:
        async with maker() as s:
            for name in ("cat/c.pdf", "cat/a.pdf", "cat/b.pdf"):
                s.add(
                    LibraryImportFile(
                        path=name,
                        sha256="f" * 64,
                        source="s",
                        version="v",
                        edition="e",
                        parse_status="parsed",
                        extract_status="pending",
                        embed_status="pending",
                        index_status="pending",
                    )
                )
            await s.commit()
            return await scope_file_ids(s, 2)

    ids = asyncio.run(_go())

    async def _names() -> list[str]:
        async with maker() as s:
            rows = (
                (
                    await s.execute(
                        sa.select(LibraryImportFile).where(
                            LibraryImportFile.id.in_(ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_id = {r.id: r.path for r in rows}
            return [by_id[i] for i in ids]

    assert asyncio.run(_names()) == ["cat/a.pdf", "cat/b.pdf"]
    asyncio.run(engine.dispose())
