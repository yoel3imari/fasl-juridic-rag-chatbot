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


def chunk_id(
    source: str, version: str, edition: str, article_or_section: str | None, text: str
) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{source}:{version}:{edition}:{article_or_section or 'section'}:{h}"


def build_provenance(entry, chunk) -> dict:
    return {
        "source": entry.source,
        "version": entry.version,
        "edition": entry.edition.value
        if hasattr(entry.edition, "value")
        else str(entry.edition),
        "pub_date": entry.pub_date,
        "doc_date": entry.doc_date,
        "hijri_date": entry.hijri_date,
        "language": entry.language,
        "article_or_section": chunk.article_or_section,
        "coverage_note": entry.coverage_note,
        "hierarchy": chunk.hierarchy,
        "collection": "legal_authorities",  # never a matter collection
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
        for chunk in chunks:
            prov = build_provenance(entry, chunk)
            rec = {
                "id": chunk_id(
                    entry.source,
                    entry.version,
                    str(prov["edition"]),
                    chunk.article_or_section,
                    chunk.text,
                ),
                **prov,
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
