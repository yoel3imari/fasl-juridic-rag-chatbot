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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.library_import import LibraryImportFile

BACKEND_DIR = Path(__file__).resolve().parents[2]
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


async def bump_indexed_count(
    session: AsyncSession, file_id: int, delta: int = 1
) -> None:
    """Atomically increment ``indexed_count`` with a single UPDATE statement.

    Never read-modify-write this counter: a single statement keeps the
    increment atomic under SQLite's writer serialization, so two concurrent
    writers cannot lose an update.
    """
    await session.execute(
        update(LibraryImportFile)
        .where(LibraryImportFile.id == file_id)
        .values(indexed_count=LibraryImportFile.indexed_count + delta)
    )


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


def atomic_write_json(path: str | Path, payload: Any) -> Path:
    """Write JSON atomically: temp file in the same dir + ``os.replace``.

    A crash before ``os.replace`` leaves the previous file byte-identical;
    a crash during ``os.replace`` is atomic on POSIX. The temp file is
    removed if serialization or the write fails, so no partial JSON or
    stray ``.tmp`` files survive.
    """
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
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


def export_manifest(
    rows: Sequence[Any],
    manifest_path: str | Path = DEFAULT_MANIFEST,
    seed_state_path: str | Path = DEFAULT_SEED_STATE,
) -> dict:
    """Regenerate BOTH derived JSON files from SQLite ledger rows, atomically.

    This is the ONLY sanctioned writer of the two JSON files. Each file is
    written via :func:`atomic_write_json`; the manifest is replaced first,
    then the seed state. Deterministic key/entry ordering makes re-exports
    byte-identical (idempotent).
    """
    manifest_payload = build_manifest_payload(rows)
    seed_payload = build_seed_state_payload(rows)
    atomic_write_json(manifest_path, manifest_payload)
    atomic_write_json(seed_state_path, seed_payload)
    return {
        "manifest_entries": len(manifest_payload["entries"]),
        "state_keys": len(seed_payload),
    }
