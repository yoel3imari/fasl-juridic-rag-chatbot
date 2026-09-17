"""Lawyer-review audit fields on drafts (task 9).

Revision ID: 0003_review_fields
Revises: 0002_sections
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_review_fields"
down_revision: str | None = "0002_sections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("drafts", sa.Column("reviewer", sa.String(length=255), nullable=True))
    op.add_column(
        "drafts", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("drafts", "reviewed_at")
    op.drop_column("drafts", "reviewer")
