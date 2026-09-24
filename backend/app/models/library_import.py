"""SQLite bulk-import ledger: files, chunks, and migration/reembed run sentinels.

SQLite is the SOURCE OF TRUTH for bulk-ingest state. The JSON files under
``backend/data/`` (``library-manifest.json`` / ``library-seed-state.json``)
are DERIVED artifacts regenerated only via
:func:`app.library.bulk_state.export_manifest`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.sqlite import JSON as SQLITE_JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LibraryImportFile(Base):
    """One catalogued source file and its per-stage status."""

    __tablename__ = "library_import_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(255), nullable=False)
    edition: Mapped[str] = mapped_column(String(50), nullable=False)
    parse_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    extract_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    embed_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    index_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LibraryImportChunk(Base):
    """One chunk derived from a catalogued file and its index state."""

    __tablename__ = "library_import_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("library_import_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ord: Mapped[int] = mapped_column("ord", Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LibraryImportRun(Base):
    """Sentinel row for collection migration / matter re-embed runs."""

    __tablename__ = "library_import_runs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('migrate', 'reembed-matter')", name="ck_library_import_runs_kind"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    payload_json: Mapped[dict | None] = mapped_column(SQLITE_JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
