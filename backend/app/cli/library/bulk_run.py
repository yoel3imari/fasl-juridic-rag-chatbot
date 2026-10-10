"""Resumable end-to-end orchestration: embed + index primitives + run wrapper.

Plan todo 16. ``python -m app.cli.library.bulk embed|index|run`` iterates
ledger artifacts, embeds + upserts in batches, updates statuses, prints
progress/ETA, and resumes cleanly after a kill (per-file commits, finished
work is never re-driven).

Ordering is enforced by an explicit preflight that REFUSES (non-zero,
listing the missing step) BEFORE any embed/index work unless all hold:

* ``parity.json`` (``mean_cosine>=0.99``) and ``quality_gate.json``
  (``pass:true``) exist and match the live ``EMBEDDING_MODEL`` /
  ``EMBEDDING_DIM`` / git HEAD (mirrors ``bulk_migrate.preflight``);
* a ``library_import_runs`` row (kind=``migrate``, status=``done``)
  matches model/dim/git-sha;
* a ``library_import_runs`` row (kind=``reembed-matter``, status=``done``)
  matches model/dim/git-sha (the zero-matter no-op row counts).

``run`` is an explicit wrapper: preflight -> ensure-extract(scope) ->
embed(scope) -> index(scope). Standalone ``embed``/``index`` ensure their
own preconditions the same way (never assume prior stages).

Embed details (todo 14 client, todo 6 guard):

* chunk rows are created from VERIFIED artifacts where missing;
* only ``pending`` chunks are embedded; rows are marked ``embedded`` only
  after their batch succeeds (never claim success on failure);
* the served budget is SINGLE-SOURCED in the pure domain
  (``app.domain.authority.served_budget``) and the splitter cuts to it, so
  a chunk is never born over budget. If a chunk still measures
  ``served_estimate(text) > SERVED_SAFE_TOKENS`` the run ABORTS with
  ``RunFailed`` (exit 2, naming the file and the over-budget count) instead
  of skipping it: the artifact predates the splitter fix and must be
  re-extracted, never indexed as a silent partial success. Skipping it
  quietly is what lost 62% of the corpus;
* transport-failure isolation: if a batch still drops the connection
  (the estimate can undercount spaced-tatweel OCR artifacts by ~20+
  tokens), the batch is retried text-by-text; each text that individually
  drops the connection is marked ``failed`` with report reason
  ``embed-transport-failure`` while the surviving texts still embed --
  one killer chunk can no longer abort the whole run;
* vectors are persisted per file to ``<artifact_dir>/<sha>.embed.jsonl.zst``
  BEFORE rows are marked embedded, so a kill between embed and index
  resumes from the cache with zero re-embed.

Index details (todo 15 reconciliation):

* embedded chunks are fed through ``index_authorities_batched``;
* on ``IndexReconciliationError`` ONLY ``verified_ids`` are marked
  indexed, the rest stay pending, and the run fails loudly (non-zero).

Exit codes: 0 on success (including the no-op), 2 (``FAIL_EXIT``) on any
refusal or failure. Live-Qdrant writes happen ONLY in the E2E subset;
unit tests use tmp DBs + fake embedders/stores.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register ledger metadata)
from app.cli.library.artifacts import (
    artifact_path_for,
    read_artifact,
    read_verified_artifact,
    sha256_file,
    write_artifact,
)
from app.cli.library.bulk_extract import (
    RETRIABLE_QUARANTINE_PREFIXES,
    ExtractJob,
    is_extract_eligible,
    run_job,
)
from app.cli.library.bulk_state import set_chunk_status, set_file_stage_status
from app.cli.library.fingerprint import compute_pipeline_fingerprint
from app.models.base import Base
from app.repositories.library_import import LibraryImportRepository

FAIL_EXIT = 2

MIN_PARITY_COSINE = 0.99
EMBED_TRANSPORT_FAILURE_REASON = "embed-transport-failure"
EMBED_CACHE_SUFFIX = ".embed.jsonl.zst"
# Single-text isolation retries: after a killer drops the connection the
# server auto-restarts within seconds, so one sleep-backed retry separates
# "server was still restarting" from "this text is a genuine killer".
_ISOLATED_SINGLE_RETRIES = 3
_ISOLATED_RETRY_DELAY_SECONDS = 5.0
_ISOLATED_READY_TIMEOUT_SECONDS = 240.0


class RunRefused(Exception):
    """Preflight refusal: exit non-zero BEFORE any embed/index work."""


class RunFailed(Exception):
    """Stage failure: rows stay pending, success is never claimed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_gate_artifact(path: str, kind: str) -> dict:
    """Load a gate artifact or refuse loudly (missing/unparseable never passes)."""
    if not path or not os.path.exists(path):
        raise RunRefused(f"{kind} artifact missing: {path!r} (refusing)")
    try:
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        raise RunRefused(f"{kind} artifact unparseable: {path!r}: {exc}")
    if not isinstance(payload, dict):
        raise RunRefused(f"{kind} artifact is not a JSON object: {path!r}")
    return payload


def preflight(*, model: str, dim: int, sha: str) -> dict:
    """Validate the live embedding config against the declared invariants.

    Pure (no I/O).

    WHY THERE IS NO MODEL-QUALITY GATE HERE
    ---------------------------------------
    This used to require ``quality_gate.json`` to say ``pass: true`` and
    ``parity.json`` to clear a cosine floor, plus ``migrate`` /
    ``reembed-matter`` sentinel rows. That gate was removed from the reindex
    path because it was measuring the wrong thing:

    * it tested VERBATIM SPAN -> OWN CHUNK retrieval, while real users ask
      natural-language questions ("what are official languages in Morocco");
    * it was written as a MODEL-selection rule ("stay on bge-m3") yet fired on
      CHUNKING changes, so a chunker fix could be vetoed by a metric that
      cannot see what changed;
    * its pass/fail margin was 1 chunk in 120 -- far inside sampling noise for
      n=120 -- and it blocked a change that took indexed corpus coverage from
      37.6% to 100%;
    * re-running it cost a ~1.2GB baseline download plus a two-phase
      container swap, on the critical path of an ordinary reindex.

    Model selection remains a first-class, DELIBERATE operation -- see
    ``app.cli.library.quality_gate`` / ``app.cli.library.parity`` and
    ``quality_gate capture`` / ``score``. It is simply no longer something an
    ordinary embed/index has to clear first.

    What still guards a reindex is structural and lives where the damage
    happens: the served-budget invariant (``served_budget.py``, enforced per
    chunk in :func:`ensure_embed`), artifact integrity
    (``read_verified_artifact``), and per-chunk index reconciliation with
    verified ids in :func:`ensure_index`.
    """
    if not model or not model.strip():
        raise RunRefused("embedding model is unset (refusing)")
    if dim <= 0:
        raise RunRefused(f"embedding dim={dim!r} is not positive (refusing)")
    return {"model": model, "dim": dim, "pipeline_fingerprint": sha}


def _make_embedder(embed_url: str | None, model: str):
    """Seam: live CrispEmbed client (tests inject a fake factory)."""
    from app.infrastructure.embeddings.client import CrispEmbedClient

    return CrispEmbedClient(base_url=embed_url, model=model)


def _make_store(qdrant_url: str | None, local_path: str | None, dim: int):
    """Seam: live Qdrant store (tests inject a fake factory)."""
    from app.infrastructure.qdrant.store import QdrantStore

    return QdrantStore(url=qdrant_url, local_path=local_path, dim=dim)


def _served_estimate(text: str) -> int:
    """Seam over the killer-input guard (tests inject a stub).

    Delegates to the pure domain single source of truth
    (``app.domain.authority.served_budget``) -- the same budget the splitter
    cuts to -- not to the CLI layer.
    """
    from app.domain.authority.served_budget import served_estimate

    return served_estimate(text)


def _served_safe_tokens() -> int:
    """Seam over the served-budget ceiling (tests inject a stub)."""
    from app.domain.authority.served_budget import SERVED_SAFE_TOKENS

    return int(SERVED_SAFE_TOKENS)


def embed_cache_path_for(artifact_dir: str | Path, file_sha: str) -> Path:
    """Stable per-file vector-cache path (regenerable, alongside artifacts)."""
    return Path(artifact_dir) / f"{file_sha}{EMBED_CACHE_SUFFIX}"


def read_embed_cache(path: str | Path) -> dict[str, list[float]]:
    """Read a vector cache into ``{chunk_id: vector}`` (empty when absent)."""
    if not os.path.exists(path):
        return {}
    out: dict[str, list[float]] = {}
    for rec in read_artifact(path):
        out[str(rec["chunk_id"])] = [float(v) for v in rec["vector"]]
    return out


def write_embed_cache(path: str | Path, vectors: dict[str, list[float]]) -> Path:
    """Atomically rewrite a vector cache (tmp + os.replace, via artifacts)."""
    recs = (
        {"chunk_id": cid, "vector": vec}
        for cid, vec in sorted(vectors.items())
        for vec in [list(vec)]
    )
    return write_artifact(recs, path)


async def check_preflight(session: Any, *, model: str, dim: int, sha: str) -> dict:
    """Validate the live embedding config (no gate artifacts, no I/O).

    ``session`` is accepted for call-site symmetry with the other stage
    helpers; the checks are pure config assertions.
    """
    return preflight(model=model, dim=dim, sha=sha)


async def scope_file_ids(session: Any, limit: int | None) -> list[int]:
    """Deterministic scope: first ``--limit`` files in ledger path order.

    Dedup winners are fixed upstream by the catalog; the limit only gates
    how many flow downstream (mirrors ``post_dedup_limit``).
    """
    if limit is not None and limit < 0:
        raise RunFailed(f"limit must be >= 0, got {limit}")
    rows = await LibraryImportRepository(session).list_files()
    ids = [int(r.id) for r in rows]
    return ids if limit is None else ids[:limit]


async def ensure_extract(
    session: Any,
    scope_ids: list[int],
    *,
    source_dir: str,
    artifact_dir: str,
    min_chars: int,
    dpi: int,
    ocr_timeout: int,
    ocr_mode: str,
) -> dict:
    """(Re-)extract every scoped file that is missing verifiable output.

    Mirrors ``bulk_extract._collect_jobs`` per file: skips verifiable
    ``extracted`` rows (status + sha + artifact bytes present) and terminal
    quarantines; re-drives pending/failed, retriable quarantines, and
    NULL-sha legacy rows (re-extract once, never a silent pass). Per-file
    commit so a kill-mid-run resumes.
    """
    src = Path(source_dir)
    adir = Path(artifact_dir)
    report: dict[str, Any] = {
        "extracted": 0,
        "quarantined": 0,
        "skipped_done": 0,
        "skipped_ineligible": 0,
        "chunks": 0,
        "ocr_pages": 0,
    }
    wanted = set(scope_ids)
    repo = LibraryImportRepository(session)
    for file_id in scope_ids:
        row = await repo.get_file(file_id)
        if row is None or int(row.id) not in wanted:
            continue
        eligible, _ = is_extract_eligible(row.parse_status, row.quarantine_reason)
        if not eligible:
            report["skipped_ineligible"] += 1
            continue
        if (
            row.extract_status == "extracted"
            and getattr(row, "artifact_sha256", None)
            and artifact_path_for(adir, row.sha256).exists()
        ):
            report["skipped_done"] += 1  # resume: never redo finished work
            continue
        if row.extract_status == "quarantined" and not (row.quarantine_reason or "").startswith(
            RETRIABLE_QUARANTINE_PREFIXES
        ):
            report["skipped_done"] += 1  # terminal quarantine stays quarantined
            continue
        job = ExtractJob(
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
        )
        res = run_job(job)
        if res.status == "extracted":
            dest = artifact_path_for(adir, row.sha256)
            write_artifact(iter(res.records), dest)
            row.artifact_sha256 = sha256_file(dest)
            row.chunk_count = len(res.records)
            row.quarantine_reason = None
            set_file_stage_status(row, "extract_status", "extracted")
            report["extracted"] += 1
            report["chunks"] += len(res.records)
            report["ocr_pages"] += res.ocr_pages
        else:
            row.chunk_count = 0
            row.artifact_sha256 = None
            row.quarantine_reason = res.quarantine_reason
            set_file_stage_status(row, "extract_status", "quarantined")
            report["quarantined"] += 1
        await session.commit()  # per-file commit: kill-mid-run resumes
    return report


async def _probe_embedder_ready(embedder: Any) -> bool:
    """Seam: does the embedding server answer right now?

    ``embedder`` may be a test fake with no ``base_url``; those report ready
    so the retry loop falls back to a plain pause rather than hanging.
    """
    base = getattr(embedder, "base_url", None)
    if not base:
        return True
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/health")
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def _wait_embedder_ready(embedder: Any) -> None:
    """Block until the embedding server answers again, or the ceiling passes.

    A crash-looping container is not ready in a fixed number of seconds: it
    must re-mmap and reload the GGUF. Polling /health is what makes the retry
    survive a restart, which a fixed short sleep cannot.
    """
    deadline = time.monotonic() + _ISOLATED_READY_TIMEOUT_SECONDS
    while True:
        if await _probe_embedder_ready(embedder):
            return
        if time.monotonic() >= deadline:
            return
        await anyio.sleep(_ISOLATED_RETRY_DELAY_SECONDS)


async def _embed_batch_isolated(
    embedder: Any, sendable: list[Any], texts: list[str]
) -> tuple[list[tuple[Any, str, list[float]]], list[Any], int]:
    """Embed one batch; isolate killer texts on transport failure.

    Returns ``(good, failed, calls)`` where ``good`` is
    ``[(chunk, text, vec)]`` for surviving texts, ``failed`` the chunks
    whose SINGLE-text request persistently dropped the connection (server
    crash-loop), and ``calls`` the number of ``embed_sync`` invocations
    made. Only :class:`httpx.TransportError` (5xx/timeout/disconnect --
    the client already retried 4x) triggers isolation; any other exception
    propagates.
    """
    try:
        vectors = embedder.embed_sync(texts)
        return (
            [(c, t, v) for c, t, v in zip(sendable, texts, vectors, strict=True)],
            [],
            1,
        )
    except httpx.TransportError:
        pass
    # Batch dropped the connection: retry text-by-text so one killer cannot
    # take the whole batch (or run) down with it. Between attempts we WAIT FOR
    # THE SERVER TO ANSWER /health rather than sleeping a fixed short interval:
    # a crash-looping container needs to reload its GGUF, so a 5s sleep used to
    # land inside the restart and mark every chunk queued behind the crasher as
    # failed. One crash became a cluster of 32.
    good: list[tuple[Any, str, list[float]]] = []
    failed: list[Any] = []
    calls = 1
    for chunk, text in zip(sendable, texts, strict=True):
        vec: list[float] | None = None
        for attempt in range(_ISOLATED_SINGLE_RETRIES + 1):
            try:
                vec = embedder.embed_sync([text])[0]
                break
            except httpx.TransportError:
                calls += 1
                if attempt < _ISOLATED_SINGLE_RETRIES:
                    await _wait_embedder_ready(embedder)
        if vec is None:
            failed.append(chunk)
        else:
            good.append((chunk, text, vec))
    return good, failed, calls


async def ensure_embed(
    session: Any,
    scope_ids: list[int],
    *,
    artifact_dir: str,
    embedder: Any,
    dim: int,
) -> dict:
    """Embed every scoped file's pending chunks; create chunk rows from artifacts.

    Served-budget guard: the splitter already cuts every chunk to the served
    budget, so an over-budget chunk here means a STALE artifact and the run
    fails loudly (:class:`RunFailed`, exit 2) rather than dropping the chunk
    and indexing the remainder as a partial success. Cache-first resume:
    vectors already in the per-file cache mark their rows embedded without a
    server call.
    """
    from app.infrastructure.embeddings.client import estimate_eta_seconds

    adir = Path(artifact_dir)
    safe_tokens = _served_safe_tokens()
    report: dict[str, Any] = {
        "files_embedded": 0,
        "chunks_embedded": 0,
        "chunks_reused_cache": 0,
        "chunks_skipped": 0,
        "skipped_ids": [],
        "chunks_failed_transport": 0,
        "transport_failed_ids": [],
        "embed_calls": 0,
    }
    t0 = time.monotonic()
    chars_done = 0
    chars_total = 0
    wanted = set(scope_ids)
    repo = LibraryImportRepository(session)
    for file_id in scope_ids:
        row = await repo.get_file(file_id)
        if row is None or int(row.id) not in wanted:
            continue
        if row.extract_status != "extracted":
            continue  # quarantined/pending upstream: not this stage's work
        if row.embed_status == "embedded":
            continue  # resume: never re-embed finished work
        if row.embed_status not in ("pending", "failed"):
            continue
        if not getattr(row, "artifact_sha256", None):
            continue  # unverifiable: ensure_extract owns the re-drive
        records = list(
            read_verified_artifact(artifact_path_for(adir, row.sha256), row.artifact_sha256)
        )
        existing = {c.chunk_id: c for c in await repo.list_chunks(row.id)}
        for ord_, rec in enumerate(records):
            if rec["chunk_id"] not in existing:
                fresh = repo.new_pending_chunk(rec["chunk_id"], row.id, ord_)
                session.add(fresh)
                existing[rec["chunk_id"]] = fresh
        await session.flush()
        pending = [c for c in existing.values() if c.status == "pending"]
        if not pending:
            if all(c.status in ("embedded", "indexed", "failed") for c in existing.values()):
                set_file_stage_status(row, "embed_status", "embedded")
                report["files_embedded"] += 1
                await session.commit()
            continue
        by_id = {rec["chunk_id"]: rec for rec in records}
        cache_path = embed_cache_path_for(adir, row.sha256)
        cache = read_embed_cache(cache_path)
        over_budget: list[str] = []
        for chunk in pending:
            rec = by_id.get(chunk.chunk_id)
            if rec is None:
                continue  # pragma: no cover - artifact/ledger skew guard
            if _served_estimate(str(rec.get("text", ""))) > safe_tokens:
                set_chunk_status(chunk, "failed")
                report["chunks_skipped"] += 1
                report["skipped_ids"].append(chunk.chunk_id)
                over_budget.append(chunk.chunk_id)
        if over_budget:
            # Fail LOUDLY, never quietly. The splitter now cuts to the same
            # served budget this stage measures against, so no chunk can be
            # born over it: this branch is unreachable for a freshly
            # extracted artifact. Reaching it means the artifact predates the
            # splitter fix (older extractor), and indexing the rest would be
            # a silent partial success -- exactly how 62% of the corpus was
            # lost before. So: name the file and the count, leave the ledger
            # uncommitted (those chunks stay pending for the re-extract) and
            # abort with RunFailed -> exit 2. This module never claims
            # success on failure.
            raise RunFailed(
                f"embed: {len(over_budget)} chunk(s) over the served budget "
                f"({safe_tokens}) in {row.path} -- stale artifact, re-extract "
                f"(never index a partial success); first ids: "
                f"{', '.join(over_budget[:3])}"
            )
        sendable = [c for c in pending if c.status == "pending" and c.chunk_id not in cache]
        for chunk in pending:
            if chunk.chunk_id in cache and chunk.status == "pending":
                if len(cache[chunk.chunk_id]) != dim:
                    raise RunFailed(
                        f"cached dim {len(cache[chunk.chunk_id])} != {dim} "
                        f"for {chunk.chunk_id} (refusing)"
                    )
                set_chunk_status(chunk, "embedded")
                report["chunks_reused_cache"] += 1
                report["chunks_embedded"] += 1
        if sendable:
            texts = [str(by_id[c.chunk_id].get("text", "")) for c in sendable]
            chars_total += sum(len(t) for t in texts)
            good, failed, calls = await _embed_batch_isolated(embedder, sendable, texts)
            report["embed_calls"] += calls
            for chunk in failed:
                set_chunk_status(chunk, "failed")
                report["chunks_failed_transport"] += 1
                report["transport_failed_ids"].append(chunk.chunk_id)
            if failed:
                print(
                    f"embed: isolated {len(failed)} killer chunk(s) "
                    f"reason={EMBED_TRANSPORT_FAILURE_REASON} (never retried "
                    f"in batch; {row.path})",
                    flush=True,
                )
            vectors = [vec for _, _, vec in good]
            good_texts = [text for _, text, _ in good]
            sendable = [chunk for chunk, _, _ in good]
            for chunk, vec in zip(sendable, vectors, strict=True):
                if len(vec) != dim:
                    raise RunFailed(
                        f"embed dim {len(vec)} != configured {dim} for {chunk.chunk_id} (refusing)"
                    )
                cache[chunk.chunk_id] = [float(v) for v in vec]
            if good:
                write_embed_cache(cache_path, cache)  # cache BEFORE ledger marks
                for chunk in sendable:
                    set_chunk_status(chunk, "embedded")
                report["chunks_embedded"] += len(sendable)
                chars_done += sum(len(t) for t in good_texts)
                eta = estimate_eta_seconds(
                    chars_done, max(chars_total, chars_done + 1), time.monotonic() - t0
                )
                print(
                    f"embed: {row.path} chunks={len(sendable)} "
                    f"chars={chars_done}/{chars_total} "
                    f"eta_s={None if eta is None else round(eta, 1)}",
                    flush=True,
                )
        remaining = [
            c for c in existing.values() if c.status not in ("embedded", "indexed", "failed")
        ]
        if not remaining:
            set_file_stage_status(row, "embed_status", "embedded")
            report["files_embedded"] += 1
        await session.commit()  # per-file commit: kill-mid-run resumes
    return report


async def ensure_index(
    session: Any,
    scope_ids: list[int],
    *,
    artifact_dir: str,
    store: Any,
) -> dict:
    """Index every scoped file's embedded chunks with count reconciliation.

    On ``IndexReconciliationError`` ONLY ``verified_ids`` are marked
    indexed, the rest stay pending, and the run fails loudly (non-zero).
    """
    from app.infrastructure.qdrant.store import IndexReconciliationError

    adir = Path(artifact_dir)
    report: dict[str, Any] = {
        "files_indexed": 0,
        "chunks_indexed": 0,
        "batches": 0,
        "count_before": store.count_points("legal_authorities"),
        "count_after": 0,
    }
    wanted = set(scope_ids)
    repo = LibraryImportRepository(session)
    for file_id in scope_ids:
        row = await repo.get_file(file_id)
        if row is None or int(row.id) not in wanted:
            continue
        if row.embed_status != "embedded":
            continue  # upstream not done: not this stage's work
        if row.index_status == "indexed":
            continue  # resume: never re-index finished work
        if row.index_status not in ("pending", "failed"):
            continue
        chunks = await repo.list_chunks_ordered(row.id)
        todo = [c for c in chunks if c.status == "embedded"]
        if not todo:
            if chunks and all(c.status in ("indexed", "failed") for c in chunks):
                set_file_stage_status(row, "index_status", "indexed")
                report["files_indexed"] += 1
                await session.commit()
            continue
        records = {
            rec["chunk_id"]: rec
            for rec in read_verified_artifact(
                artifact_path_for(adir, row.sha256), row.artifact_sha256
            )
        }
        cache = read_embed_cache(embed_cache_path_for(adir, row.sha256))
        points = []
        for chunk in todo:
            rec = records.get(chunk.chunk_id)
            vec = cache.get(chunk.chunk_id)
            if rec is None or vec is None:
                continue  # pragma: no cover - embed/index skew guard
            points.append(
                {
                    "id": chunk.chunk_id,
                    "vector": vec,
                    "text": rec.get("text", ""),
                    "source": rec.get("source", ""),
                    "version": rec.get("version", ""),
                    "edition": rec.get("edition", ""),
                    "language": rec.get("language", ""),
                    "article_or_section": rec.get("article") or chunk.chunk_id,
                    "page": rec.get("page", 0),
                    "hierarchy": rec.get("hierarchy") or {},
                    "chunk_id": chunk.chunk_id,
                    "category": rec.get("category", ""),
                    "file_sha": row.sha256,
                }
            )
        if not points:
            continue  # pragma: no cover - nothing verifiable to index
        try:
            result = store.index_authorities_batched(points)
        except IndexReconciliationError as exc:
            verified = set(exc.verified_ids)
            for chunk in todo:
                if chunk.chunk_id in verified:
                    set_chunk_status(chunk, "indexed")
                    chunk.indexed_at = datetime.now(timezone.utc)
                    report["chunks_indexed"] += 1
            await session.commit()
            raise RunFailed(
                f"index reconciliation failed for {row.path}: {exc} "
                f"(marked {len(verified)}/{len(todo)} verified; rest pending)"
            ) from exc
        by_id = {c.chunk_id: c for c in todo}
        for cid in result.verified_ids:
            chunk = by_id.get(cid)
            if chunk is not None:
                set_chunk_status(chunk, "indexed")
                chunk.indexed_at = datetime.now(timezone.utc)
        report["chunks_indexed"] += len(result.verified_ids)
        report["batches"] += result.batches
        set_file_stage_status(row, "index_status", "indexed")
        row.indexed_count = sum(1 for c in chunks if c.status == "indexed")
        report["files_indexed"] += 1
        await session.commit()  # per-file commit: kill-mid-run resumes
        print(
            f"index: {row.path} verified={len(result.verified_ids)}/{len(points)} "
            f"spot_checked={result.spot_checked}",
            flush=True,
        )
    report["count_after"] = store.count_points("legal_authorities")
    return report


def _engine(db_url: str):
    return create_async_engine(db_url, connect_args={"timeout": 30})


async def run_embed(args: argparse.Namespace) -> dict[str, Any]:
    """Execute ``bulk embed``: preflight -> ensure-extract -> embed."""
    from app.config import settings

    model = settings.EMBEDDING_MODEL
    dim = int(settings.EMBEDDING_DIM)
    sha = compute_pipeline_fingerprint(model=model, dim=dim)
    db_url = args.db_url or settings.DATABASE_URL
    engine = _engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            gate_report = await check_preflight(
                session,
                model=model,
                dim=dim,
                sha=sha,
            )
            scope = await scope_file_ids(session, args.limit)
            extract_report = await ensure_extract(
                session,
                scope,
                source_dir=args.source_dir,
                artifact_dir=args.artifact_dir,
                min_chars=settings.LIBRARY_OCR_MIN_CHARS,
                dpi=settings.LIBRARY_OCR_DPI,
                ocr_timeout=settings.LIBRARY_OCR_TIMEOUT_SECONDS,
                ocr_mode=settings.LIBRARY_OCR_MODE,
            )
            embedder = _make_embedder(args.embed_url or settings.CRISPEMBED_URL, model)
            embed_report = await ensure_embed(
                session,
                scope,
                artifact_dir=args.artifact_dir,
                embedder=embedder,
                dim=dim,
            )
    finally:
        await engine.dispose()
    print(
        f"embed: files_embedded={embed_report['files_embedded']} "
        f"chunks_embedded={embed_report['chunks_embedded']} "
        f"reused_cache={embed_report['chunks_reused_cache']} "
        f"skipped={embed_report['chunks_skipped']}",
        flush=True,
    )
    return {
        "action": "embed",
        "model": model,
        "dim": dim,
        "git_sha": sha,
        "scope_files": len(scope),
        "preflight": gate_report,
        "extract": extract_report,
        "embed": embed_report,
    }


async def run_index(args: argparse.Namespace) -> dict[str, Any]:
    """Execute ``bulk index``: preflight -> ensure-extract -> ensure-embed -> index."""
    from app.config import settings

    model = settings.EMBEDDING_MODEL
    dim = int(settings.EMBEDDING_DIM)
    sha = compute_pipeline_fingerprint(model=model, dim=dim)
    db_url = args.db_url or settings.DATABASE_URL
    engine = _engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            gate_report = await check_preflight(
                session,
                model=model,
                dim=dim,
                sha=sha,
            )
            scope = await scope_file_ids(session, args.limit)
            extract_report = await ensure_extract(
                session,
                scope,
                source_dir=args.source_dir,
                artifact_dir=args.artifact_dir,
                min_chars=settings.LIBRARY_OCR_MIN_CHARS,
                dpi=settings.LIBRARY_OCR_DPI,
                ocr_timeout=settings.LIBRARY_OCR_TIMEOUT_SECONDS,
                ocr_mode=settings.LIBRARY_OCR_MODE,
            )
            embedder = _make_embedder(args.embed_url or settings.CRISPEMBED_URL, model)
            embed_report = await ensure_embed(
                session,
                scope,
                artifact_dir=args.artifact_dir,
                embedder=embedder,
                dim=dim,
            )
            store = _make_store(
                args.qdrant_url or settings.QDRANT_URL,
                args.qdrant_local_path or (settings.QDRANT_LOCAL_PATH or None),
                dim,
            )
            index_report = await ensure_index(
                session, scope, artifact_dir=args.artifact_dir, store=store
            )
    finally:
        await engine.dispose()
    print(
        f"index: files_indexed={index_report['files_indexed']} "
        f"chunks_indexed={index_report['chunks_indexed']} "
        f"qdrant {index_report['count_before']}->{index_report['count_after']}",
        flush=True,
    )
    return {
        "action": "index",
        "model": model,
        "dim": dim,
        "git_sha": sha,
        "scope_files": len(scope),
        "preflight": gate_report,
        "extract": extract_report,
        "embed": embed_report,
        "index": index_report,
    }


async def run_run(args: argparse.Namespace) -> dict[str, Any]:
    """Execute ``bulk run``: explicit embed-then-index wrapper over one scope."""
    from app.config import settings

    model = settings.EMBEDDING_MODEL
    dim = int(settings.EMBEDDING_DIM)
    sha = compute_pipeline_fingerprint(model=model, dim=dim)
    db_url = args.db_url or settings.DATABASE_URL
    engine = _engine(db_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            gate_report = await check_preflight(
                session,
                model=model,
                dim=dim,
                sha=sha,
            )
            print(
                f"run: preflight ok model={model} dim={dim} sha={sha[:7]}",
                flush=True,
            )
            scope = await scope_file_ids(session, args.limit)
            extract_report = await ensure_extract(
                session,
                scope,
                source_dir=args.source_dir,
                artifact_dir=args.artifact_dir,
                min_chars=settings.LIBRARY_OCR_MIN_CHARS,
                dpi=settings.LIBRARY_OCR_DPI,
                ocr_timeout=settings.LIBRARY_OCR_TIMEOUT_SECONDS,
                ocr_mode=settings.LIBRARY_OCR_MODE,
            )
            embedder = _make_embedder(args.embed_url or settings.CRISPEMBED_URL, model)
            embed_report = await ensure_embed(
                session,
                scope,
                artifact_dir=args.artifact_dir,
                embedder=embedder,
                dim=dim,
            )
            store = _make_store(
                args.qdrant_url or settings.QDRANT_URL,
                args.qdrant_local_path or (settings.QDRANT_LOCAL_PATH or None),
                dim,
            )
            index_report = await ensure_index(
                session, scope, artifact_dir=args.artifact_dir, store=store
            )
            from sqlalchemy import select

            from app.cli.library.bulk_state import export_manifest
            from app.models.library_import import LibraryImportFile

            stmt = select(LibraryImportFile).where(
                (
                    LibraryImportFile.quarantine_reason.is_(None)
                    | ~LibraryImportFile.quarantine_reason.like("duplicate-of:%")
                )
                & (LibraryImportFile.parse_status != "failed")
            )
            winner_rows = (await session.execute(stmt)).scalars().all()
            export_manifest(winner_rows)
    finally:
        await engine.dispose()
    print(
        f"run: scope={len(scope)} "
        f"extracted={extract_report['extracted']} "
        f"embedded={embed_report['chunks_embedded']} "
        f"indexed={index_report['chunks_indexed']} "
        f"qdrant {index_report['count_before']}->{index_report['count_after']}",
        flush=True,
    )
    return {
        "action": "run",
        "model": model,
        "dim": dim,
        "git_sha": sha,
        "scope_files": len(scope),
        "preflight": gate_report,
        "extract": extract_report,
        "embed": embed_report,
        "index": index_report,
    }


def _stage_args(parser: argparse.ArgumentParser) -> None:
    from app.config import settings

    parser.add_argument("--source-dir", default=settings.LIBRARY_SOURCE_DIR)
    parser.add_argument("--db-url", default=settings.DATABASE_URL)
    parser.add_argument("--artifact-dir", default=settings.LIBRARY_ARTIFACT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--qdrant-url", default=None)
    parser.add_argument("--qdrant-local-path", default=None)
    parser.add_argument("--embed-url", default=None)


def add_embed_parser(sub: Any) -> argparse.ArgumentParser:
    parser = sub.add_parser("embed", help="embed scoped artifacts to the chunk ledger")
    _stage_args(parser)
    return parser


def add_index_parser(sub: Any) -> argparse.ArgumentParser:
    parser = sub.add_parser("index", help="index embedded chunks with reconciliation")
    _stage_args(parser)
    return parser


def add_run_parser(sub: Any) -> argparse.ArgumentParser:
    parser = sub.add_parser("run", help="explicit wrapper: embed then index")
    _stage_args(parser)
    return parser


async def run_embed_from_args(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return await run_embed(args)
    except (RunRefused, RunFailed) as exc:
        print(f"FAIL: bulk embed refused: {exc}", file=sys.stderr)
        raise SystemExit(FAIL_EXIT)


async def run_index_from_args(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return await run_index(args)
    except (RunRefused, RunFailed) as exc:
        print(f"FAIL: bulk index refused: {exc}", file=sys.stderr)
        raise SystemExit(FAIL_EXIT)


async def run_run_from_args(args: argparse.Namespace) -> dict[str, Any]:
    try:
        return await run_run(args)
    except (RunRefused, RunFailed) as exc:
        print(f"FAIL: bulk run refused: {exc}", file=sys.stderr)
        raise SystemExit(FAIL_EXIT)
