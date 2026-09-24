"""Per-artifact sha256 for round-trip verification (todo 13).

Revision ID: 0005_artifact_sha256
Revises: 0004_library_bulk_import

Existing rows keep NULL until a re-run extracts them again; resume logic
must treat NULL as "needs verification/re-extract", never as passing.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_artifact_sha256"
down_revision: str | None = "0004_library_bulk_import"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "library_import_files",
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("library_import_files", "artifact_sha256")
