"""Bulk-import ledger tables (todo 7: SQLite source of truth).

Revision ID: 0004_library_bulk_import
Revises: 0003_review_fields
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_library_bulk_import"
down_revision: str | None = "0003_review_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "library_import_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("category", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("version", sa.String(length=255), nullable=False),
        sa.Column("edition", sa.String(length=50), nullable=False),
        sa.Column(
            "parse_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "extract_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "embed_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "index_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("indexed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quarantine_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path", name="uq_library_import_files_path"),
    )
    op.create_index(
        "ix_library_import_files_sha256", "library_import_files", ["sha256"]
    )
    op.create_table(
        "library_import_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chunk_id", sa.String(length=255), nullable=False),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("ord", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["file_id"], ["library_import_files.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", name="uq_library_import_chunks_chunk_id"),
    )
    op.create_index(
        "ix_library_import_chunks_file_id", "library_import_chunks", ["file_id"]
    )
    op.create_table(
        "library_import_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="running"
        ),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('migrate', 'reembed-matter')", name="ck_library_import_runs_kind"
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("library_import_runs")
    op.drop_index(
        "ix_library_import_chunks_file_id", table_name="library_import_chunks"
    )
    op.drop_table("library_import_chunks")
    op.drop_index("ix_library_import_files_sha256", table_name="library_import_files")
    op.drop_table("library_import_files")
