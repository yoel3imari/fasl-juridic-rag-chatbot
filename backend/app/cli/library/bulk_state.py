"""SQLite-vs-JSON ownership for the bulk-import ledger (todo 7).

Ownership rule: SQLite (``library_import_*`` tables) is the SOURCE OF TRUTH. ``library-manifest.json`` and ``library-seed-state.json`` are
DERIVED artifacts regenerated ONLY by :func:`export_manifest` via an
atomic temp-file + ``os.replace`` write. Never rewrite the JSON per file.

Single-writer discipline for multiprocess extraction:

* exactly ONE process (the bulk orchestrator parent) writes to SQLite and
  to the derived JSON. Extract workers are pure functions: they return
  ``(chunks, artifact_bytes)`` to the parent over IPC and never open the
  DB or the JSON files themselves;
* the parent serializes its own writes through :func:`single_writer`, an
  advisory ``flock`` guard that raises :class:`WriterBusyError` if a
  second writer ever starts;
* concurrent counter updates must use :func:`bump_indexed_count`, a single
  ``UPDATE ... SET indexed_count = indexed_count + :delta`` statement, so
  two writers can never lose an increment via read-modify-write.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library_import import LibraryImportFile
from app.repositories.library_import import LibraryImportRepository

BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = BACKEND_DIR / "data" / "library-manifest.json"
DEFAULT_SEED_STATE = BACKEND_DIR / "data" / "library-seed-state.json"
DEFAULT_WRITER_LOCK = BACKEND_DIR / "data" / ".library-writer.lock"

RUN_KINDS = frozenset({"migrate", "reembed-matter"})

# Per-column stage vocabularies. ``*_DONE`` values are terminal: re-runs
# skip them instead of regressing them (idempotent resume).
FILE_STAGE_DONE = {
    "parse_status": "parsed",
    "extract_status": "extracted",
    "embed_status": "embedded",
    "index_status": "indexed",
}
FILE_STAGE_VALUES = {
    "parse_status": frozenset({"pending", "parsed", "quarantined", "failed"}),
    "extract_status": frozenset({"pending", "extracted", "quarantined", "failed"}),
    "embed_status": frozenset({"pending", "embedded", "failed"}),
    "index_status": frozenset({"pending", "indexed", "failed"}),
}
CHUNK_STATUS_VALUES = frozenset({"pending", "embedded", "indexed", "failed"})
CHUNK_DONE = "indexed"

STAGE_UPSTREAM: dict[str, tuple[str, ...]] = {
    "extract_status": (),
    "embed_status": ("extract_status",),
    "index_status": ("extract_status", "embed_status"),
}
# Statuses at a stage that a re-run must re-drive (never skip).
REDRIVE_STATUSES = frozenset({"pending", "failed"})


class WriterBusyError(RuntimeError):
    """Raised when a second bulk writer tries to acquire the writer lock."""


class InvalidTransitionError(ValueError):
    """Raised when a ledger status transition is illegal."""


def validate_run_kind(kind: str) -> str:
    """Parse ``kind`` at the boundary; interior code receives a valid kind."""
    if kind not in RUN_KINDS:
        raise ValueError(
            f"invalid library_import_runs kind {kind!r}; expected one of {sorted(RUN_KINDS)}"
        )
    return kind


def set_file_stage_status(row: LibraryImportFile, stage: str, value: str) -> None:
    """Advance one per-stage status column with idempotent, no-regression rules.

    Rules: same value is a no-op (idempotent re-run); a terminal DONE value
    never regresses; ``failed``/``quarantined`` may retry via ``pending`` or
    complete directly; unknown values raise.
    """
    if stage not in FILE_STAGE_VALUES:
        raise InvalidTransitionError(f"unknown file stage {stage!r}")
    if value not in FILE_STAGE_VALUES[stage]:
        raise InvalidTransitionError(f"invalid {stage} value {value!r}")
    current = getattr(row, stage)
    if value == current:
        return
    if current == FILE_STAGE_DONE[stage]:
        raise InvalidTransitionError(
            f"{stage} is terminal at {current!r}; refusing regression to {value!r}"
        )
    setattr(row, stage, value)


def set_chunk_status(row: Any, value: str) -> None:
    """Advance a chunk status with the same idempotent, no-regression rules."""
    if value not in CHUNK_STATUS_VALUES:
        raise InvalidTransitionError(f"invalid chunk status {value!r}")
    current = getattr(row, "status")
    if value == current:
        return
    if current == CHUNK_DONE:
        raise InvalidTransitionError(
            f"chunk status is terminal at {current!r}; refusing regression to {value!r}"
        )
    setattr(row, "status", value)


def _is_indexed(row: Any) -> bool:
    """True when the file already reached the terminal `indexed` state."""
    return _get(row, "index_status") == FILE_STAGE_DONE["index_status"]


def assert_stage_ready(row: Any, stage: str) -> None:
    """Enforce pipeline order: refuse `stage` unless every upstream stage is DONE.

    Pure (no DB side effects). Raises :class:`InvalidTransitionError` when an
    upstream stage is not at its terminal DONE value, or when the file is
    already terminally `indexed` (nothing downstream left to do).
    """
    if stage not in STAGE_UPSTREAM:
        raise InvalidTransitionError(f"unknown pipeline stage {stage!r}")
    if _is_indexed(row):
        raise InvalidTransitionError("file is already indexed; refusing re-drive")
    for upstream in STAGE_UPSTREAM[stage]:
        if _get(row, upstream) != FILE_STAGE_DONE[upstream]:
            raise InvalidTransitionError(
                f"{stage} requires {upstream}={FILE_STAGE_DONE[upstream]!r}, "
                f"found {_get(row, upstream)!r}"
            )


def files_for_extract(
    rows: Sequence[Any],
    *,
    redrive_quarantine_prefixes: tuple[str, ...] = (),
) -> list[Any]:
    """Pure resume selection for the extract stage (no DB side effects).

    Skips verifiable `extracted` rows (extract_status + artifact sha present)
    and terminal quarantines; re-drives `pending`/`failed`, retriable
    quarantines (reason prefix match), and `extracted` rows with no artifact
    sha (pre-todo-13 rows: needs re-extract, never a silent pass). Files
    already terminally `indexed` are always skipped. Order preserved.
    """
    out: list[Any] = []
    for row in rows:
        if _is_indexed(row):
            continue
        status = _get(row, "extract_status")
        if status == FILE_STAGE_DONE["extract_status"]:
            if _get(row, "artifact_sha256"):
                continue  # verifiable finished work: never redo
            out.append(row)  # extracted but unverifiable: re-drive
        elif status in REDRIVE_STATUSES:
            out.append(row)
        elif status == "quarantined":
            reason = _get(row, "quarantine_reason") or ""
            if redrive_quarantine_prefixes and reason.startswith(
                redrive_quarantine_prefixes
            ):
                out.append(row)
            # else: terminal quarantine stays quarantined
        # `failed` is in REDRIVE_STATUSES; anything else unknown is skipped
        # (callers surface it via the status-transition guard, not here).
    return out


def files_for_embed(rows: Sequence[Any]) -> list[Any]:
    """Pure resume selection for the embed stage (no DB side effects).

    Skips `embedded` and terminally `indexed` files (never re-embed finished
    work); re-drives `pending`/`failed` files whose extract stage is DONE.
    """
    out: list[Any] = []
    for row in rows:
        if _is_indexed(row):
            continue
        if _get(row, "embed_status") == FILE_STAGE_DONE["embed_status"]:
            continue
        if _get(row, "embed_status") not in REDRIVE_STATUSES:
            continue
        if _get(row, "extract_status") != FILE_STAGE_DONE["extract_status"]:
            continue  # upstream not done: not this stage's work
        out.append(row)
    return out


def files_for_index(rows: Sequence[Any]) -> list[Any]:
    """Pure resume selection for the index stage (no DB side effects).

    Skips terminally `indexed` files; re-drives `pending`/`failed` files
    whose embed stage is DONE.
    """
    out: list[Any] = []
    for row in rows:
        if _is_indexed(row):
            continue
        if _get(row, "index_status") not in REDRIVE_STATUSES:
            continue
        if _get(row, "embed_status") != FILE_STAGE_DONE["embed_status"]:
            continue  # upstream not done: not this stage's work
        out.append(row)
    return out


def post_dedup_limit(items: Sequence[Any], limit: int | None) -> list[Any]:
    """Gate an already-deduped winner list to `--limit` (pure).

    Dedup winners are fixed upstream by the catalog; the limit only gates how
    many flow downstream and must never change which path won a content hash.
    """
    if limit is None:
        return list(items)
    if limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
    return list(items[:limit])


async def bump_indexed_count(
    session: AsyncSession, file_id: int, delta: int = 1
) -> None:
    """Atomically increment ``indexed_count`` with a single UPDATE statement.

    Never read-modify-write this counter: a single statement keeps the
    increment atomic under SQLite's writer serialization, so two concurrent
    writers cannot lose an update. The statement itself lives in
    ``app.repositories.library_import``.
    """
    await LibraryImportRepository(session).bump_indexed_count(file_id, delta)


@contextlib.contextmanager
def single_writer(lock_path: str | Path = DEFAULT_WRITER_LOCK) -> Iterator[Any]:
    """Advisory single-writer guard for the bulk orchestrator process.

    Yields the lock handle while held. Raises :class:`WriterBusyError`
    immediately if another process already holds the lock.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WriterBusyError(
                f"another bulk writer holds {path}; refusing second writer"
            ) from exc
        try:
            yield fh
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _get(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def build_manifest_payload(rows: Sequence[Any]) -> dict:
    """Build the derived ``library-manifest.json`` payload from ledger rows."""
    entries = []
    for row in sorted(
        rows, key=lambda r: (_get(r, "source"), _get(r, "version"), _get(r, "edition"))
    ):
        entry: dict[str, Any] = {
            "source": _get(row, "source"),
            "version": _get(row, "version"),
            "edition": _get(row, "edition"),
            "file_path": _get(row, "path"),
        }
        for extra in (
            "pub_date",
            "doc_date",
            "hijri_date",
            "language",
            "coverage_note",
            "category",
            "file_sha",
        ):
            value = _get(row, extra)
            if value is not None:
                entry[extra] = value
        entries.append(entry)
    return {"entries": entries}


def build_seed_state_payload(rows: Sequence[Any]) -> dict:
    """Build the derived ``library-seed-state.json`` payload from ledger rows."""
    state: dict[str, dict[str, int]] = {}
    for row in sorted(
        rows, key=lambda r: (_get(r, "source"), _get(r, "version"), _get(r, "edition"))
    ):
        key = f"{_get(row, 'source')}@{_get(row, 'version')}#{_get(row, 'edition')}"
        state[key] = {
            "chunks": int(_get(row, "chunk_count", 0) or 0),
            "embedded": int(_get(row, "indexed_count", 0) or 0),
        }
    return state


def _dumps_canonical(payload: Any) -> bytes:
    """Canonical JSON bytes for derived files (stable across re-exports)."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode(
        "utf-8"
    )


def atomic_write_json(path: str | Path, payload: Any) -> Path:
    """Write JSON atomically: temp file in the same dir + ``os.replace``.

    A crash before ``os.replace`` leaves the previous file byte-identical;
    a crash during ``os.replace`` is atomic on POSIX. The temp file is
    removed if serialization or the write fails, so no partial JSON or
    stray ``.tmp`` files survive.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = _dumps_canonical(payload).decode("utf-8")
    fd, tmp_name = tempfile.mkstemp(
        dir=str(dest.parent), prefix=dest.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return dest


def _write_json_if_changed(path: str | Path, payload: Any) -> bool:
    """Write `payload` to `path` atomically ONLY when content changed.

    Returns True when the file was (re)written, False when the existing
    bytes were already identical (rewrite skipped: derived JSON is never
    rewritten wholesale). A missing file counts as changed.
    """
    dest = Path(path)
    wanted = _dumps_canonical(payload)
    try:
        if dest.read_bytes() == wanted:
            return False
    except OSError:
        pass  # missing/unreadable: fall through to the atomic write
    atomic_write_json(dest, payload)
    return True


def export_manifest(
    rows: Sequence[Any],
    manifest_path: str | Path = DEFAULT_MANIFEST,
    seed_state_path: str | Path = DEFAULT_SEED_STATE,
) -> dict:
    """Regenerate BOTH derived JSON files from SQLite ledger rows, atomically.

    This is the ONLY sanctioned writer of the two JSON files. Each file is
    written via :func:`atomic_write_json` ONLY when its canonical content
    changed; unchanged files are left byte- and mtime-identical (no wholesale
    rewrite). Deterministic key/entry ordering makes re-exports idempotent.
    """
    manifest_payload = build_manifest_payload(rows)
    seed_payload = build_seed_state_payload(rows)
    rewrote_manifest = _write_json_if_changed(manifest_path, manifest_payload)
    rewrote_seed = _write_json_if_changed(seed_state_path, seed_payload)
    return {
        "manifest_entries": len(manifest_payload["entries"]),
        "state_keys": len(seed_payload),
        "rewrote_manifest": rewrote_manifest,
        "rewrote_seed_state": rewrote_seed,
    }
