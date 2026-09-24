"""Todo 13 acceptance: artifact round-trip + resume/limit semantics.

No corpus access, no embedding calls, no Qdrant: tmp dirs + tiny PDFs +
in-memory/SQLite-tmp ledgers only. Embed/index stages (todos 14/15) are
modeled at the ledger/state-machine level with a fake store counter; live
Qdrant count reconciliation is todo 15's acceptance, not this file's.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register metadata)
from app.library.artifacts import (
    ArtifactIntegrityError,
    build_record,
    read_verified_artifact,
    sha256_file,
    verify_artifact,
    write_artifact,
)
from app.library.bulk_state import (
    InvalidTransitionError,
    assert_stage_ready,
    export_manifest,
    files_for_embed,
    files_for_extract,
    files_for_index,
    post_dedup_limit,
    set_chunk_status,
    set_file_stage_status,
)
from app.models.base import Base
from app.models.library_import import LibraryImportChunk, LibraryImportFile


def _tiny_pdf(path: Path, text: str = "hello") -> None:
    import fitz

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def _file(**kw) -> LibraryImportFile:
    # NOTE: SQLAlchemy Python-side `default=` applies at INSERT, not on
    # construction, so in-memory rows need explicit statuses (None otherwise).
    base = {
        "path": "cat/a.pdf",
        "sha256": "0" * 64,
        "source": "Dahir",
        "version": "1.11.151",
        "edition": "ar-general",
        "parse_status": "parsed",
        "extract_status": "pending",
        "embed_status": "pending",
        "index_status": "pending",
    }
    base.update(kw)
    return LibraryImportFile(**base)


def _rec(i: int, file_sha: str = "f" * 64) -> dict:
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


# --- artifact round-trip + sha guard ----------------------------------------


def test_artifact_sha_matches_bytes_not_just_presence(tmp_path: Path) -> None:
    # Given: an artifact written from records
    dest = tmp_path / "a.jsonl.zst"
    write_artifact(iter([_rec(0), _rec(1)]), dest)
    # When: hashing the bytes independently of the helper
    with open(dest, "rb") as fh:
        independent = hashlib.sha256(fh.read()).hexdigest()
    # Then: the helper reports the SAME digest (proves it hashes the bytes)
    assert sha256_file(dest) == independent
    assert len(independent) == 64
    # And: verification passes and the verified stream yields both records
    assert verify_artifact(dest, independent) == independent
    assert [r["chunk_id"] for r in read_verified_artifact(dest, independent)] == [
        "id-0",
        "id-1",
    ]


def test_tampered_artifact_fails_loudly(tmp_path: Path) -> None:
    # Given: a good artifact with its recorded sha
    dest = tmp_path / "a.jsonl.zst"
    write_artifact(iter([_rec(0)]), dest)
    good_sha = sha256_file(dest)
    # When: a single byte is flipped (simulated bitrot/tamper)
    raw = bytearray(dest.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    dest.write_bytes(bytes(raw))
    # Then: verification RAISES (never warns-and-continues) ...
    with pytest.raises(ArtifactIntegrityError, match="mismatch"):
        verify_artifact(dest, good_sha)
    # ... and the verified reader raises BEFORE yielding a single record
    with pytest.raises(ArtifactIntegrityError):
        list(read_verified_artifact(dest, good_sha))


def test_missing_sha_never_silently_passes(tmp_path: Path) -> None:
    # Given: an artifact from a pre-todo-13 row (sha never recorded)
    dest = tmp_path / "a.jsonl.zst"
    write_artifact(iter([_rec(0)]), dest)
    # When/Then: None AND empty string both raise (needs re-extract, not a pass)
    with pytest.raises(ArtifactIntegrityError, match="needs re-extract"):
        verify_artifact(dest, None)
    with pytest.raises(ArtifactIntegrityError, match="needs re-extract"):
        verify_artifact(dest, "")
    with pytest.raises(ArtifactIntegrityError):
        list(read_verified_artifact(dest, None))


def test_verify_missing_file_raises_not_warns(tmp_path: Path) -> None:
    # Given: a ledger sha pointing at no file (deleted artifact)
    # When/Then: the read-side check raises (FileNotFoundError), never passes
    with pytest.raises((ArtifactIntegrityError, FileNotFoundError)):
        verify_artifact(tmp_path / "gone.jsonl.zst", "0" * 64)


# --- ledger state machine ----------------------------------------------------


def test_terminal_done_never_regresses_per_stage() -> None:
    # Given: rows terminal at each stage of catalogued->extracted->embedded->indexed
    extracted = _file(extract_status="extracted")
    embedded = _file(extract_status="extracted", embed_status="embedded")
    indexed = _file(
        extract_status="extracted", embed_status="embedded", index_status="indexed"
    )
    # When/Then: every regression attempt raises (illegal transition)
    with pytest.raises(InvalidTransitionError):
        set_file_stage_status(extracted, "extract_status", "pending")
    with pytest.raises(InvalidTransitionError):
        set_file_stage_status(embedded, "embed_status", "failed")
    with pytest.raises(InvalidTransitionError):
        set_file_stage_status(indexed, "index_status", "pending")
    with pytest.raises(InvalidTransitionError):
        set_file_stage_status(indexed, "extract_status", "quarantined")


def test_pending_failed_quarantine_retry_paths() -> None:
    # Given: a failed embed and a quarantined extract
    failed = _file(extract_status="extracted", embed_status="failed")
    quarantined = _file(extract_status="quarantined")
    # When: retrying via pending, or completing directly
    set_file_stage_status(failed, "embed_status", "pending")
    set_file_stage_status(failed, "embed_status", "embedded")
    set_file_stage_status(quarantined, "extract_status", "pending")
    # Then: both reach DONE
    assert failed.embed_status == "embedded"
    assert quarantined.extract_status == "pending"


def test_chunk_indexed_is_terminal() -> None:
    # Given: an indexed chunk
    row = LibraryImportChunk(chunk_id="c1", file_id=1, ord=0, status="indexed")
    # When/Then: regression raises
    with pytest.raises(InvalidTransitionError):
        set_chunk_status(row, "pending")


def test_assert_stage_ready_enforces_pipeline_order() -> None:
    # Given: a freshly catalogued file (nothing extracted yet)
    row = _file()
    # When/Then: embed/index are refused until extract is DONE ...
    with pytest.raises(InvalidTransitionError, match="extract_status"):
        assert_stage_ready(row, "embed_status")
    with pytest.raises(InvalidTransitionError, match="extract_status"):
        assert_stage_ready(row, "index_status")
    # ... extract itself needs no upstream
    assert_stage_ready(row, "extract_status")
    # Given: extracted but not embedded
    row.extract_status = "extracted"
    assert_stage_ready(row, "embed_status")
    with pytest.raises(InvalidTransitionError, match="embed_status"):
        assert_stage_ready(row, "index_status")
    # Given: fully indexed
    row.embed_status = "embedded"
    row.index_status = "indexed"
    # When/Then: every stage refuses to re-drive it
    for stage in ("extract_status", "embed_status", "index_status"):
        with pytest.raises(InvalidTransitionError, match="already indexed"):
            assert_stage_ready(row, stage)
    # When/Then: unknown stages raise too
    with pytest.raises(InvalidTransitionError, match="unknown pipeline stage"):
        assert_stage_ready(_file(), "teleport_status")


# --- pure resume selectors ----------------------------------------------------


def _ledger() -> list[LibraryImportFile]:
    return [
        _file(
            path="done.pdf",
            extract_status="extracted",
            embed_status="embedded",
            index_status="indexed",
            artifact_sha256="a" * 64,
            chunk_count=3,
            indexed_count=3,
        ),
        _file(
            path="embedded.pdf",
            extract_status="extracted",
            embed_status="embedded",
            artifact_sha256="b" * 64,
            chunk_count=2,
        ),
        _file(
            path="extracted.pdf",
            extract_status="extracted",
            artifact_sha256="c" * 64,
            chunk_count=2,
        ),
        _file(
            path="legacy.pdf",
            extract_status="extracted",
            artifact_sha256=None,
            chunk_count=2,
        ),  # pre-todo-13 row
        _file(path="pending.pdf"),  # all stages pending
        _file(
            path="failed-embed.pdf",
            extract_status="extracted",
            embed_status="failed",
            artifact_sha256="d" * 64,
            chunk_count=2,
        ),
        _file(
            path="failed-index.pdf",
            extract_status="extracted",
            embed_status="embedded",
            index_status="failed",
            artifact_sha256="e" * 64,
            chunk_count=2,
        ),
        _file(
            path="terminal-q.pdf",
            extract_status="quarantined",
            quarantine_reason="empty-document",
        ),
        _file(
            path="retry-q.pdf",
            extract_status="quarantined",
            quarantine_reason="chunking-failed: tokenizer timeout",
        ),
    ]


def _paths(rows: list[LibraryImportFile]) -> list[str]:
    return [r.path for r in rows]


def test_extract_selector_skips_indexed_and_verifiable_redrives_rest() -> None:
    # Given: the mixed ledger
    rows = _ledger()
    # When: selecting extract work (retriable quarantine prefix configured)
    got = files_for_extract(rows, redrive_quarantine_prefixes=("chunking-failed:",))
    # Then: pending + legacy-no-sha + retriable quarantine re-driven ...
    assert _paths(got) == ["legacy.pdf", "pending.pdf", "retry-q.pdf"]
    # ... while indexed / verifiable-extracted / terminal-quarantine are skipped
    assert "done.pdf" not in _paths(got)
    assert "extracted.pdf" not in _paths(got)
    assert "terminal-q.pdf" not in _paths(got)


def test_embed_selector_never_reembeds() -> None:
    # Given: the mixed ledger
    rows = _ledger()
    # When: selecting embed work
    got = files_for_embed(rows)
    # Then: extracted-pending + legacy + failed-embed re-driven; embedded/indexed skipped
    assert set(_paths(got)) == {"extracted.pdf", "legacy.pdf", "failed-embed.pdf"}
    assert "done.pdf" not in _paths(got)
    assert "embedded.pdf" not in _paths(got)
    assert "failed-index.pdf" not in _paths(got)  # embed DONE; index's job


def test_index_selector_skips_indexed_redrives_pending_failed() -> None:
    # Given: the mixed ledger
    rows = _ledger()
    # When: selecting index work
    got = files_for_index(rows)
    # Then: embedded-pending + failed re-driven; indexed skipped
    assert set(_paths(got)) == {"embedded.pdf", "failed-index.pdf"}
    assert "done.pdf" not in _paths(got)


def test_selectors_accept_plain_dicts_and_preserve_order() -> None:
    # Given: dict rows (no ORM) in a fixed order
    rows = [
        {
            "path": "b.pdf",
            "extract_status": "extracted",
            "embed_status": "pending",
            "index_status": "pending",
            "artifact_sha256": "x" * 64,
        },
        {
            "path": "a.pdf",
            "extract_status": "pending",
            "embed_status": "pending",
            "index_status": "pending",
        },
    ]
    # When: selecting for embed
    got = files_for_embed(rows)
    # Then: only the extracted one is embed-ready (a.pdf waits on extract) ...
    assert [r["path"] for r in got] == ["b.pdf"]
    # ... and extract selection is the mirror image
    assert [r["path"] for r in files_for_extract(rows)] == ["a.pdf"]
    # Given: two embed-ready dicts in non-sorted order
    ready = [
        {
            "path": "z.pdf",
            "extract_status": "extracted",
            "embed_status": "pending",
            "index_status": "pending",
        },
        {
            "path": "m.pdf",
            "extract_status": "extracted",
            "embed_status": "failed",
            "index_status": "pending",
        },
    ]
    # When/Then: input order preserved (catalog order flows downstream)
    assert [r["path"] for r in files_for_embed(ready)] == ["z.pdf", "m.pdf"]


def test_selectors_have_no_db_side_effects() -> None:
    # Given: rows with a full DONE pipeline
    rows = _ledger()
    before = [(r.extract_status, r.embed_status, r.index_status) for r in rows]
    # When: running every selector
    files_for_extract(rows)
    files_for_embed(rows)
    files_for_index(rows)
    # Then: no status mutated (pure: selection only)
    assert [(r.extract_status, r.embed_status, r.index_status) for r in rows] == before


# --- --limit post-dedup gating --------------------------------------------------


def test_post_dedup_limit_gates_only_downstream() -> None:
    # Given: fixed dedup winners (order = catalog sorted order)
    winners = ["a.pdf", "b.pdf", "c.pdf"]
    # When: gating with --limit
    assert post_dedup_limit(winners, None) == winners
    assert post_dedup_limit(winners, 2) == ["a.pdf", "b.pdf"]
    assert post_dedup_limit(winners, 0) == []
    # Then: the full winner list is untouched (limit never recomputes dedup)
    assert winners == ["a.pdf", "b.pdf", "c.pdf"]
    # When/Then: negative limits raise instead of silently slicing
    with pytest.raises(ValueError, match="limit must be >= 0"):
        post_dedup_limit(winners, -1)


def test_catalog_limit_keeps_same_winners_prefix(tmp_path: Path) -> None:
    # Given: a tiny source tree with distinct contents
    from app.library.bulk_catalog import apply_limit, deduplicate, scan_file

    src = tmp_path / "shortlist"
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        _tiny_pdf(src / "cat" / name, f"body-{name}")
    scanned = [scan_file(src, rel) for rel in ["cat/a.pdf", "cat/b.pdf", "cat/c.pdf"]]
    winners, _ = deduplicate(scanned)
    full = [w.rel for w in winners]
    # When: applying --limit
    # Then: gated is a strict prefix of the full winners (winners fixed by catalog)
    assert [w.rel for w in apply_limit(winners, 1)] == full[:1]
    assert [w.rel for w in apply_limit(winners, None)] == full


def test_extract_collect_jobs_limit_is_post_eligibility(tmp_path: Path) -> None:
    # Given: three eligible parsed rows in path order
    from app.library.bulk_extract import _collect_jobs

    rows = [
        _file(path=f"cat/{n}.pdf", parse_status="parsed", sha256=f"{i}" * 64)
        for i, n in enumerate(("a", "b", "c"))
    ]
    # When: collecting with limit=1 (no artifacts yet => nothing skipped-done)
    jobs, ineligible, done = _collect_jobs(
        rows,
        tmp_path / "src",
        tmp_path / "art",
        min_chars=20,
        dpi=300,
        ocr_timeout=120,
        ocr_mode="auto",
        limit=1,
    )
    # Then: exactly the first path-ordered job survives (post-dedup gating)
    assert [rel for _, job in jobs for rel in [job.rel]] == ["cat/a.pdf"]
    assert (ineligible, done) == (0, 0)


# --- kill-mid-run then resume (extract level, real tmp ledger) -------------------


async def _seed_cataloged_ledger(db_url: str, src: Path, names: list[str]) -> None:
    maker = async_sessionmaker(
        create_async_engine(db_url, connect_args={"timeout": 30}),
        expire_on_commit=False,
    )
    engine = maker.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with maker() as s:
        for i, name in enumerate(names):
            s.add(
                _file(
                    path=name,
                    sha256=f"{i + 1}" * 64,
                    parse_status="parsed",
                    source="Dahir",
                    version=f"v{i}",
                    edition="ar-general",
                )
            )
        await s.commit()
    await engine.dispose()


async def test_kill_mid_run_then_resume_never_redoes_extract(tmp_path: Path) -> None:
    # Given: a tmp ledger with 3 catalogued tiny PDFs
    from app.library.artifacts import artifact_path_for
    from app.library.bulk_extract import run_extract

    src = tmp_path / "shortlist"
    names = ["a.pdf", "b.pdf", "c.pdf"]
    for i, name in enumerate(names):
        _tiny_pdf(src / name, f"المادة {i + 1}: نص قانوني كاف للاستخراج.")
    db = tmp_path / "resume.db"
    db_url = f"sqlite+aiosqlite:///{db}"
    adir = tmp_path / "artifacts"
    await _seed_cataloged_ledger(db_url, src, names)

    # When: a first run is "killed" after one file (limit=1 stands in for SIGKILL)
    first = await run_extract(
        source_dir=src, db_url=db_url, artifact_dir=adir, limit=1, workers=1
    )
    assert first["extracted"] == 1
    maker = async_sessionmaker(
        create_async_engine(db_url, connect_args={"timeout": 30}),
        expire_on_commit=False,
    )
    engine = maker.kw["bind"]
    async with maker() as s:
        rows = (
            (
                await s.execute(
                    sa.select(LibraryImportFile).order_by(LibraryImportFile.path)
                )
            )
            .scalars()
            .all()
        )
        first_row = rows[0]
        assert first_row.extract_status == "extracted"
        assert first_row.artifact_sha256 and len(first_row.artifact_sha256) == 64
        # The recorded sha really matches the artifact bytes on disk
        assert verify_artifact(
            artifact_path_for(adir, first_row.sha256), first_row.artifact_sha256
        )
        sha_before = first_row.artifact_sha256
        mtime_before = artifact_path_for(adir, first_row.sha256).stat().st_mtime_ns
    await engine.dispose()

    # When: resuming without a limit (the post-kill restart)
    second = await run_extract(
        source_dir=src, db_url=db_url, artifact_dir=adir, limit=None, workers=1
    )
    # Then: only the 2 remaining files extract; the finished one is skipped
    assert second["extracted"] == 2
    assert second["skipped"] == 1
    maker2 = async_sessionmaker(
        create_async_engine(db_url, connect_args={"timeout": 30}),
        expire_on_commit=False,
    )
    engine2 = maker2.kw["bind"]
    async with maker2() as s:
        rows = (
            (
                await s.execute(
                    sa.select(LibraryImportFile).order_by(LibraryImportFile.path)
                )
            )
            .scalars()
            .all()
        )
        assert [r.extract_status for r in rows] == ["extracted"] * 3
        # Artifact sha stable across re-runs (generated artifacts, not rewritten)
        assert rows[0].artifact_sha256 == sha_before
        assert (
            artifact_path_for(adir, rows[0].sha256).stat().st_mtime_ns == mtime_before
        )
        assert all(r.artifact_sha256 and len(r.artifact_sha256) == 64 for r in rows)
    await engine2.dispose()


async def test_legacy_extracted_row_without_sha_is_redriven(tmp_path: Path) -> None:
    # Given: a pre-todo-13 extracted row (artifact on disk, sha NULL) + artifact
    from app.library.artifacts import artifact_path_for
    from app.library.bulk_extract import run_extract

    src = tmp_path / "shortlist"
    _tiny_pdf(src / "a.pdf", "المادة 1: نص قانوني كاف للاستخراج.")
    db = tmp_path / "legacy.db"
    db_url = f"sqlite+aiosqlite:///{db}"
    adir = tmp_path / "artifacts"
    await _seed_cataloged_ledger(db_url, src, ["a.pdf"])
    maker = async_sessionmaker(
        create_async_engine(db_url, connect_args={"timeout": 30}),
        expire_on_commit=False,
    )
    engine = maker.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with maker() as s:
        row = (await s.execute(sa.select(LibraryImportFile))).scalar_one()
        row.extract_status = "extracted"  # legacy: marked done, sha never recorded
        row.artifact_sha256 = None
        row.chunk_count = 1
        await s.commit()
        file_sha = row.sha256
    await engine.dispose()
    # A stale artifact with no ledger sha sits on disk
    write_artifact(iter([_rec(0, file_sha)]), artifact_path_for(adir, file_sha))

    # When: the next run collects work
    report = await run_extract(
        source_dir=src, db_url=db_url, artifact_dir=adir, limit=None, workers=1
    )
    # Then: the legacy row was re-extracted (never silently passed) and now
    # carries a verifiable sha
    assert report["extracted"] == 1
    maker2 = async_sessionmaker(
        create_async_engine(db_url, connect_args={"timeout": 30}),
        expire_on_commit=False,
    )
    engine2 = maker2.kw["bind"]
    async with maker2() as s:
        row = (await s.execute(sa.select(LibraryImportFile))).scalar_one()
        assert row.artifact_sha256 and len(row.artifact_sha256) == 64
        verify_artifact(artifact_path_for(adir, row.sha256), row.artifact_sha256)
    await engine2.dispose()


# --- monotonic embed/index counts with a fake store (no todo-14/15 deps) -------


class _FakeVectorStore:
    """In-memory stand-in for the todo-15 indexer: counts indexed points."""

    def __init__(self) -> None:
        self.indexed: dict[str, int] = {}
        self.upsert_calls = 0

    def upsert_file(self, path: str, n_chunks: int) -> None:
        self.upsert_calls += 1
        self.indexed[path] = self.indexed.get(path, 0) + n_chunks

    def count(self) -> int:
        return sum(self.indexed.values())


def test_kill_mid_embed_index_resume_is_monotonic() -> None:
    # Given: a ledger where extract finished for 3 files, nothing embedded
    rows = [
        _file(
            path="a.pdf",
            extract_status="extracted",
            artifact_sha256="a" * 64,
            chunk_count=2,
        ),
        _file(
            path="b.pdf",
            extract_status="extracted",
            artifact_sha256="b" * 64,
            chunk_count=2,
        ),
        _file(
            path="c.pdf",
            extract_status="extracted",
            artifact_sha256="c" * 64,
            chunk_count=2,
        ),
    ]
    store = _FakeVectorStore()

    def drive_embed_index(ledger: list[LibraryImportFile], kill_after: int | None):
        """One pipeline pass over the ledger; kill_after simulates SIGKILL."""
        processed = 0
        for row in files_for_embed(ledger):
            set_file_stage_status(row, "embed_status", "embedded")
            processed += 1
            if kill_after is not None and processed >= kill_after:
                raise RuntimeError("simulated SIGKILL mid-run")
        for row in files_for_index(ledger):
            store.upsert_file(row.path, row.chunk_count)
            row.indexed_count = row.chunk_count
            set_file_stage_status(row, "index_status", "indexed")

    # When: the first pass is killed after embedding 1 file (nothing indexed)
    with pytest.raises(RuntimeError, match="SIGKILL"):
        drive_embed_index(rows, kill_after=1)
    embedded_after_kill = sum(1 for r in rows if r.embed_status == "embedded")
    assert embedded_after_kill == 1
    assert store.count() == 0
    # When: resuming to completion (no kill)
    drive_embed_index(rows, kill_after=None)
    # Then: embedded/indexed counts are monotonic, nothing re-embedded, and
    # the fake store count reconciles with the ledger
    assert [r.embed_status for r in rows] == ["embedded"] * 3
    assert [r.index_status for r in rows] == ["indexed"] * 3
    assert store.count() == sum(r.indexed_count for r in rows) == 6
    # When: a third pass runs (steady-state resume)
    calls_before = store.upsert_calls
    drive_embed_index(rows, kill_after=None)
    # Then: pure no-op — no file re-embedded, no point re-upserted
    assert store.upsert_calls == calls_before
    assert store.count() == 6


# --- derived JSON never rewritten wholesale -------------------------------------


def test_export_manifest_skips_rewrite_when_unchanged(tmp_path: Path) -> None:
    # Given: ledger rows exported once
    rows = [_file(chunk_count=2, indexed_count=1)]
    manifest = tmp_path / "library-manifest.json"
    seed = tmp_path / "library-seed-state.json"
    out1 = export_manifest(rows, manifest, seed)
    assert out1["rewrote_manifest"] is True and out1["rewrote_seed_state"] is True
    mtime_m, mtime_s = (
        manifest.stat().st_mtime_ns,
        seed.stat().st_mtime_ns,
    )
    # When: exporting identical content again
    out2 = export_manifest(rows, manifest, seed)
    # Then: rewrite skipped, files byte- and mtime-identical
    assert out2["rewrote_manifest"] is False and out2["rewrote_seed_state"] is False
    assert (manifest.stat().st_mtime_ns, seed.stat().st_mtime_ns) == (mtime_m, mtime_s)
    # When: the ledger actually changes
    rows[0].chunk_count = 5
    out3 = export_manifest(rows, manifest, seed)
    # Then: only the changed file (seed state) is rewritten
    assert out3["rewrote_manifest"] is False
    assert out3["rewrote_seed_state"] is True
    state = json.loads(seed.read_text(encoding="utf-8"))
    assert state["Dahir@1.11.151#ar-general"] == {"chunks": 5, "embedded": 1}
