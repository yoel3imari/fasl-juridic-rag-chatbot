"""Make conversations.matter_id nullable (matterless chat conversations).

Revision ID: 0006_conversation_matter_optional
Revises: 0005_artifact_sha256

WHY: `POST /api/v1/chat` no longer requires a matter. The LLM classifies
user intent, so a conversation may exist with matter_id = NULL (authority
retrieval only, no private evidence scope), and the column must accept it.

SQLite cannot drop a NOT NULL constraint with a plain ALTER TABLE, so the
change runs through alembic's batch mode, which rebuilds the table (create
new, copy rows, swap). The rebuild must keep the matter_id index and the
ON DELETE CASCADE foreign key to matters.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_conversation_matter_optional"
down_revision: str | None = "0005_artifact_sha256"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.alter_column("matter_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.alter_column("matter_id", existing_type=sa.Integer(), nullable=False)
