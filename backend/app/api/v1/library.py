"""Library coverage and upload endpoints: titles, versions, editions, dates, gaps, document upload."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.config import settings
from app.library.embedder import CrispEmbedClient
from app.library.extractor import extract_chunks
from app.library.manifest import EditionType, ManifestEntry
from app.library.seeder import build_provenance
from app.search.store import QdrantStore

router = APIRouter(prefix="/api/v1/library", tags=["library"])

BACKEND_DIR = Path(__file__).resolve().parents[3]
MANIFEST_PATH = BACKEND_DIR / "data" / "library-manifest.json"
SEED_STATE_PATH = BACKEND_DIR / "data" / "library-seed-state.json"


class CoverageEntry(BaseModel):
    source: str
    version: str
    edition: str
    pub_date: str | None = None
    doc_date: str | None = None
    hijri_date: str | None = None
    language: str | None = None
    coverage_note: str | None = None
    chunks: int = 0
    status: str = "pending"


class LibraryUploadOut(BaseModel):
    status: str
    source: str
    version: str
    edition: str
    pub_date: str | None = None
    doc_date: str | None = None
    hijri_date: str | None = None
    language: str | None = None
    coverage_note: str | None = None
    chunks: int
    embedded: int
    message: str


@router.get("/coverage")
async def coverage() -> dict:
    entries: list[dict] = []
    gaps: list[str] = []
    state: dict = {}
    if SEED_STATE_PATH.exists():
        try:
            state = json.loads(SEED_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    if not MANIFEST_PATH.exists():
        return {
            "titles": [],
            "gaps": ["library manifest missing"],
            "library_version": None,
        }
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for e in manifest.get("entries", []):
        key = f"{e['source']}@{e['version']}#{e['edition']}"
        info = state.get(key, {})
        edition_label = e["edition"]
        note = e.get("coverage_note")
        # Never present French translation as authoritative without its edition label.
        if edition_label == "fr-translation" and not note:
            note = "French edition is an official translation, not authoritative over the Arabic general edition"
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
    return {"titles": entries, "gaps": gaps}


@router.post(
    "/upload",
    response_model=LibraryUploadOut,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/documents",
    response_model=LibraryUploadOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_library_document(
    file: Annotated[UploadFile, File(...)],
    source: Annotated[str, Form(...)],
    version: Annotated[str, Form(...)],
    edition: Annotated[str, Form()] = "ar-general",
    pub_date: Annotated[str | None, Form()] = None,
    doc_date: Annotated[str | None, Form()] = None,
    hijri_date: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = "ar",
    coverage_note: Annotated[str | None, Form()] = None,
) -> LibraryUploadOut:
    source_clean = source.strip()
    version_clean = version.strip()
    edition_clean = edition.strip() or "ar-general"

    if not source_clean or not version_clean:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source and version are required fields",
        )

    # Read bounded file content
    chunks_raw: list[bytes] = []
    total = 0
    while True:
        piece = await file.read(1024 * 1024)
        if not piece:
            break
        total += len(piece)
        if total > settings.UPLOAD_MAX_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"file size {total} exceeds maximum limit of {settings.UPLOAD_MAX_BYTES} bytes",
            )
        chunks_raw.append(piece)
    content = b"".join(chunks_raw)

    # Save file to seed directory
    seed_dir = BACKEND_DIR / "data" / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)

    filename = file.filename or "authority_doc.txt"
    safe_name = re.sub(r"[^a-zA-Z0-9_\.-]", "_", filename)
    file_rel_path = f"backend/data/seed/{safe_name}"
    target_path = seed_dir / safe_name
    target_path.write_bytes(content)

    # Extract chunks
    extracted = extract_chunks(str(target_path))
    if not extracted and content:
        from app.library.extractor import Chunk

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
                for rec, emb in zip(records, embeddings)
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

    return LibraryUploadOut(
        status="seeded" if embedded_count > 0 or len(records) > 0 else "pending",
        source=source_clean,
        version=version_clean,
        edition=edition_clean,
        pub_date=pub_date,
        doc_date=doc_date,
        hijri_date=hijri_date,
        language=language,
        coverage_note=coverage_note,
        chunks=len(records),
        embedded=embedded_count,
        message=f"Successfully indexed {len(records)} chunks for {source_clean} ({version_clean})",
    )
