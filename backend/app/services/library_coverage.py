"""Library coverage aggregation and authority-document upload without HTTP concerns."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import BACKEND_ROOT, settings
from app.infrastructure.authority.extractor import extract_chunks
from app.infrastructure.authority.manifest import ManifestEntry
from app.infrastructure.embeddings.client import CrispEmbedClient
from app.infrastructure.qdrant.store import QdrantStore
from app.repositories.library_import import read_ledger_summary, zero_ledger_summary
from app.services.library_seed import build_provenance

# Data-dir paths derive from the single BACKEND_ROOT anchor (app.config)
# instead of per-file __file__ depth arithmetic; see its comment for why.
MANIFEST_PATH = BACKEND_ROOT / "data" / "library-manifest.json"
SEED_STATE_PATH = BACKEND_ROOT / "data" / "library-seed-state.json"

# Task 17: bounded lists. Old clients keep reading `titles`/`gaps` unchanged;
# entries past the cap are only counted, never reordered or reshaped.
COVERAGE_LIST_CAP = 200
LEDGER_MISSING_GAP = "library ledger missing (summary unavailable)"


def _ledger_db_path() -> Path | None:
    """Resolve the SQLite ledger file from settings at request time.

    Returns None when DATABASE_URL is not a file-backed sqlite URL, so the
    endpoint degrades to a zero summary instead of raising.
    """
    url = settings.DATABASE_URL
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if raw in ("", ":memory:"):
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        resolved = BACKEND_ROOT / candidate
        if resolved.exists():
            candidate = resolved
        else:
            candidate = Path.cwd() / candidate
    return candidate


def _zero_summary() -> dict:
    """The all-zero ledger aggregate; the shape lives in the repository."""
    return zero_ledger_summary()


async def _ledger_summary() -> tuple[dict, str | None]:
    """Return (summary, gap): read-only ledger aggregate, zeroed on failure."""
    db_path = _ledger_db_path()
    if db_path is None or not db_path.exists():
        return _zero_summary(), LEDGER_MISSING_GAP
    try:
        summary = await read_ledger_summary(db_path)
    except Exception:
        return _zero_summary(), LEDGER_MISSING_GAP
    return summary, None


async def coverage() -> dict:
    entries: list[dict] = []
    gaps: list[str] = []
    state: dict = {}
    if SEED_STATE_PATH.exists():
        try:
            state = json.loads(SEED_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    summary, ledger_gap = await _ledger_summary()
    if not MANIFEST_PATH.exists():
        manifest_gaps = ["library manifest missing"]
        if ledger_gap is not None:
            manifest_gaps.append(ledger_gap)
        return {
            "titles": [],
            "gaps": manifest_gaps,
            "library_version": None,
            "summary": summary,
            "titles_truncated": 0,
            "gaps_truncated": 0,
        }
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for e in manifest.get("entries", []):
        key = f"{e['source']}@{e['version']}#{e['edition']}"
        info = state.get(key, {})
        edition_label = e["edition"]
        note = e.get("coverage_note")
        # Never present French translation as authoritative without its edition label.
        if edition_label == "fr-translation" and not note:
            note = (
                "French edition is an official translation, "
                "not authoritative over the Arabic general edition"
            )
        entries.append(
            {
                "source": e["source"],
                "version": e["version"],
                "edition": edition_label,
                "pub_date": e.get("pub_date"),
                "doc_date": e.get("doc_date"),
                "hijri_date": e.get("hijri_date"),
                "language": e.get("language"),
                "coverage_note": note,
                "chunks": info.get("chunks", 0),
                "status": "seeded" if key in state else "pending",
            }
        )
        if key not in state:
            gaps.append(f"not yet seeded: {key}")
    if ledger_gap is not None:
        gaps.append(ledger_gap)
    titles_truncated = max(0, len(entries) - COVERAGE_LIST_CAP)
    gaps_truncated = max(0, len(gaps) - COVERAGE_LIST_CAP)
    return {
        "titles": entries[:COVERAGE_LIST_CAP],
        "gaps": gaps[:COVERAGE_LIST_CAP],
        "library_version": settings.LIBRARY_VERSION,
        "summary": summary,
        "titles_truncated": titles_truncated,
        "gaps_truncated": gaps_truncated,
    }


async def upload_library_document(
    *,
    content: bytes,
    filename: str,
    source: str,
    version: str,
    edition: str,
    pub_date: str | None,
    doc_date: str | None,
    hijri_date: str | None,
    language: str | None,
    coverage_note: str | None,
) -> dict:
    source_clean = source.strip()
    version_clean = version.strip()
    edition_clean = edition.strip() or "ar-general"

    # Save file to seed directory
    seed_dir = BACKEND_ROOT / "data" / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^a-zA-Z0-9_\.-]", "_", filename)
    file_rel_path = f"backend/data/seed/{safe_name}"
    target_path = seed_dir / safe_name
    target_path.write_bytes(content)

    # Extract chunks
    extracted = extract_chunks(str(target_path))
    if not extracted and content:
        from app.infrastructure.authority.extractor import Chunk

        text_str = content.decode("utf-8", errors="ignore")
        if text_str.strip():
            extracted = [
                Chunk(
                    text=text_str.strip(),
                    article_or_section="General",
                    hierarchy={"code": source_clean},
                )
            ]

    # Build provenance & records
    entry_model = ManifestEntry(
        source=source_clean,
        version=version_clean,
        edition=edition_clean,  # type: ignore[arg-type]
        file_path=file_rel_path,
        pub_date=pub_date,
        doc_date=doc_date,
        hijri_date=hijri_date,
        language=language,
        coverage_note=coverage_note,
    )

    records = []
    for ordinal, chunk in enumerate(extracted):
        prov = build_provenance(entry_model, chunk, ordinal=ordinal)
        rec = {
            "id": prov["chunk_id"],
            **prov,
            "text": chunk.text,
        }
        records.append(rec)

    # Attempt embedding & Qdrant upsert off the event loop. Both are sync
    # blocking calls, so they run in a worker thread: a slow embedding
    # service must never stall other requests sharing the loop.
    embedded_count = 0
    try:
        from anyio import to_thread

        embedder = CrispEmbedClient()
        if records:
            texts = [c.text for c in extracted]
            embeddings = await to_thread.run_sync(lambda: embedder.embed_sync(texts))
            embedded_count = len(embeddings)
            points = [
                {
                    "id": rec["id"],
                    "vector": emb,
                    "text": rec["text"],
                    **rec,
                }
                for rec, emb in zip(records, embeddings, strict=False)
            ]
            store = QdrantStore()
            await to_thread.run_sync(lambda: store.upsert_authorities(points))
    except Exception:
        # Embedder or Qdrant might be offline / in unit tests; keep metadata
        embedded_count = 0

    # Update seed state
    state = {}
    if SEED_STATE_PATH.exists():
        try:
            state = json.loads(SEED_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    key = f"{source_clean}@{version_clean}#{edition_clean}"
    state[key] = {
        "chunks": len(records),
        "embedded": embedded_count,
    }
    SEED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Update manifest
    manifest_data = {"entries": []}
    if MANIFEST_PATH.exists():
        try:
            manifest_data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            manifest_data = {"entries": []}

    entries = manifest_data.get("entries", [])
    entry_dict = {
        "source": source_clean,
        "version": version_clean,
        "edition": edition_clean,
        "file_path": file_rel_path,
        "pub_date": pub_date,
        "doc_date": doc_date,
        "hijri_date": hijri_date,
        "language": language,
        "coverage_note": coverage_note,
    }
    updated = False
    for i, e in enumerate(entries):
        if (
            e.get("source") == source_clean
            and e.get("version") == version_clean
            and e.get("edition") == edition_clean
        ):
            entries[i] = entry_dict
            updated = True
            break
    if not updated:
        entries.append(entry_dict)
    manifest_data["entries"] = entries
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "status": "seeded" if embedded_count > 0 or len(records) > 0 else "pending",
        "source": source_clean,
        "version": version_clean,
        "edition": edition_clean,
        "pub_date": pub_date,
        "doc_date": doc_date,
        "hijri_date": hijri_date,
        "language": language,
        "coverage_note": coverage_note,
        "chunks": len(records),
        "embedded": embedded_count,
        "message": (
            f"Successfully indexed {len(records)} chunks for {source_clean} ({version_clean})"
        ),
    }
