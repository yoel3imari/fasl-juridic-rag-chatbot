"""Main seeding pipeline: manifest -> extract -> provenance -> embed (authority-only)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.library.embedder import CrispEmbedClient
from app.library.extractor import extract_chunks
from app.library.manifest import LibraryManifest

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_MANIFEST = BACKEND_DIR / "data" / "library-manifest.json"
SEED_STATE = BACKEND_DIR / "data" / "library-seed-state.json"


def resolve_seed_path(file_path: str) -> str:
    """Resolve a manifest file_path against cwd, backend dir, and repo root (manual-import friendly)."""
    candidates = [Path(file_path), BACKEND_DIR / file_path, REPO_ROOT / file_path]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return file_path


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> LibraryManifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return LibraryManifest(**data)


def _coerce_int(value: object, default: int = 0) -> int:
    """Deterministic int coercion: None/garbage -> default, never raises."""
    if value is None:
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def chunk_id(
    source: str,
    version: str,
    edition: str,
    article_or_section: str | None,
    text: str,
    page: int | None = None,
    ordinal: int | None = None,
) -> str:
    """Deterministic chunk id preserving the legacy prefix.

    Legacy prefix ``source:version:edition:article:hash`` is kept verbatim;
    a deterministic ``:p<page>:o<ordinal>`` suffix keeps intra-file duplicate
    article text distinct. ``page``/``ordinal`` default to 0 so legacy callers
    (no page info) keep working and stay deterministic.
    """
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    prefix = f"{source}:{version}:{edition}:{article_or_section or 'section'}:{h}"
    return f"{prefix}:p{_coerce_int(page)}:o{_coerce_int(ordinal)}"


def build_provenance(
    entry,
    chunk,
    ordinal: int | None = None,
    category: str | None = None,
    file_sha: str | None = None,
) -> dict:
    edition = (
        entry.edition.value if hasattr(entry.edition, "value") else str(entry.edition)
    )
    page = _coerce_int(getattr(chunk, "page", None))
    hierarchy = dict(getattr(chunk, "hierarchy", None) or {})
    ord_i = _coerce_int(ordinal)
    cat = category if category is not None else (getattr(entry, "category", None) or "")
    sha = file_sha if file_sha is not None else (getattr(entry, "file_sha", None) or "")
    cid = chunk_id(
        entry.source,
        entry.version,
        edition,
        chunk.article_or_section,
        chunk.text,
        page=getattr(chunk, "page", None),
        ordinal=ord_i,
    )
    return {
        "source": entry.source,
        "version": entry.version,
        "edition": edition,
        "pub_date": entry.pub_date,
        "doc_date": entry.doc_date,
        "hijri_date": entry.hijri_date,
        "language": entry.language,
        "article_or_section": chunk.article_or_section,
        "coverage_note": entry.coverage_note,
        "hierarchy": hierarchy,
        "collection": "legal_authorities",  # never a matter collection
        "page": page,
        "chunk_id": cid,
        "category": cat,
        "file_sha": sha,
    }


def seed(manifest_path: str | Path = DEFAULT_MANIFEST, embed: bool = True) -> dict:
    """Seed the authority library. Idempotent per (source, version, edition)."""
    manifest = load_manifest(manifest_path)
    state: dict = {}
    if SEED_STATE.exists():
        state = json.loads(SEED_STATE.read_text(encoding="utf-8"))
    client = CrispEmbedClient() if embed else None
    results: list[dict] = []
    for entry in manifest.entries:
        key = f"{entry.source}@{entry.version}#{entry.edition.value if hasattr(entry.edition, 'value') else entry.edition}"
        chunks = extract_chunks(resolve_seed_path(entry.file_path))
        records = []
        for ordinal, chunk in enumerate(chunks):
            prov = build_provenance(entry, chunk, ordinal=ordinal)
            rec = {
                "id": prov["chunk_id"],
                **prov,
                "text": chunk.text,
            }
            records.append(rec)
        embeddings: list = []
        if client and records:
            try:
                embeddings = client.embed_sync([c.text for c in chunks])
            except (
                Exception
            ) as exc:  # CrispEmbed down -> record gap, don't crash seed metadata
                results.append(
                    {
                        "entry": key,
                        "chunks": len(records),
                        "embedded": 0,
                        "error": str(exc),
                        "status": "pending-embedding",
                    }
                )
                continue
        state[key] = {
            "chunks": len(records),
            "embedded": len(embeddings) if client else 0,
        }
        results.append(
            {
                "entry": key,
                "chunks": len(records),
                "embedded": len(embeddings) if client else 0,
                "status": "seeded",
            }
        )
    SEED_STATE.parent.mkdir(parents=True, exist_ok=True)
    SEED_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"results": results, "state": state}


if __name__ == "__main__":
    import sys

    # Default: metadata-only seed (no CrispEmbed required); pass --embed to embed.
    do_embed = "--embed" in sys.argv
    print(json.dumps(seed(embed=do_embed), ensure_ascii=False, indent=2))
