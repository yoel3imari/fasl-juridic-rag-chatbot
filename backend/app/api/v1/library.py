"""Library coverage endpoint: titles, versions, editions, dates, gaps."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

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


@router.get("/coverage")
async def coverage() -> dict:
    entries: list[dict] = []
    gaps: list[str] = []
    state: dict = {}
    if SEED_STATE_PATH.exists():
        state = json.loads(SEED_STATE_PATH.read_text(encoding="utf-8"))
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
