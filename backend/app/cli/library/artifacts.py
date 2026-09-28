"""zstd JSONL artifact codec + fixed record schema for bulk extraction.

Artifacts live at ``<LIBRARY_ARTIFACT_DIR>/<file_sha>.jsonl.zst`` (content-keyed,
stable across re-runs). Both directions stream: the corpus is never joined in
memory. Writes are atomic (tmp file + ``os.replace``), mirroring the todo-7
JSON discipline. The framing is real zstd: ``zstd -dc <artifact> | wc -l``
decodes it independently of this module.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Iterator

RECORD_FIELDS = (
    "chunk_id",
    "text",
    "page",
    "hierarchy",
    "article",
    "tokens",
    "source",
    "version",
    "edition",
    "category",
    "file_sha",
)


def build_record(
    *,
    chunk_id: str,
    text: str,
    page: int,
    hierarchy: dict,
    article: str | None,
    tokens: int,
    source: str,
    version: str,
    edition: str,
    category: str,
    file_sha: str,
) -> dict[str, Any]:
    """Build one artifact record with exactly the fixed 11-field schema."""
    return {
        "chunk_id": chunk_id,
        "text": text,
        "page": page,
        "hierarchy": hierarchy,
        "article": article,
        "tokens": tokens,
        "source": source,
        "version": version,
        "edition": edition,
        "category": category,
        "file_sha": file_sha,
    }


def artifact_path_for(artifact_dir: str | Path, file_sha: str) -> Path:
    """Stable artifact path keyed by content hash (idempotent re-runs)."""
    return Path(artifact_dir) / f"{file_sha}.jsonl.zst"


def write_artifact(records: Iterable[dict[str, Any]], dest: str | Path) -> Path:
    """Stream records as zstd JSONL (tmp file + atomic replace)."""
    import zstandard as zstd

    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(out.parent), prefix=out.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            with zstd.ZstdCompressor(level=3).stream_writer(fh) as comp:
                for rec in records:  # streaming: one record at a time
                    comp.write(
                        (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
                    )
        os.replace(tmp_name, out)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return out


def read_artifact(path: str | Path) -> Iterator[dict[str, Any]]:
    """Stream-decode a zstd JSONL artifact (never loads it whole)."""
    import zstandard as zstd

    with open(path, "rb") as fh:
        with zstd.ZstdDecompressor().stream_reader(fh) as reader:
            text = io.TextIOWrapper(reader, encoding="utf-8")
            for line in text:
                if line.strip():
                    yield json.loads(line)


class ArtifactIntegrityError(ValueError):
    """Raised when an artifact's bytes do not match the ledger sha256."""


def sha256_file(path: str | Path) -> str:
    """Stream SHA-256 of a file (artifacts included). Raises if missing."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(path: str | Path, expected_sha: str | None) -> str:
    """Recompute the artifact sha256 and raise loudly on mismatch.

    Returns the actual sha on success. ``None``/empty ``expected_sha``
    (pre-todo-13 rows) raises: a missing sha is "needs re-extract", never
    a silent pass.
    """
    if not expected_sha:
        raise ArtifactIntegrityError(
            f"{path}: no artifact_sha256 recorded; needs re-extract, not a pass"
        )
    actual = sha256_file(path)
    if actual != expected_sha:
        raise ArtifactIntegrityError(
            f"{path}: artifact sha mismatch: expected {expected_sha}, got {actual}"
        )
    return actual


def read_verified_artifact(
    path: str | Path, expected_sha: str | None
) -> Iterator[dict[str, Any]]:
    """Verify the artifact sha BEFORE yielding a single record, then stream it."""
    verify_artifact(path, expected_sha)
    yield from read_artifact(path)
