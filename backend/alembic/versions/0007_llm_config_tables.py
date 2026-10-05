"""LLM config tables: provider registry, credentials, active-settings singleton.

Revision ID: 0007_llm_config_tables
Revises: 0006_conversation_matter_optional

WHY: the four user-settable LLM fields (provider, model, api_keys, base_urls)
move out of plaintext ``<STORAGE_DIR>/llm_settings.json`` into SQLite, alongside
the rest of the app's state, in the existing ``matters.db``.

The three ``op.create_table`` calls are guarded by ``inspector.has_table`` because
the settings store self-bootstraps the same tables with
``Table.create(checkfirst=True)`` so LLM settings keep working before migrations
have run. A developer who started the app first must not hit "table already
exists", so re-running this revision over a bootstrapped database is a no-op
instead of an error.

The provider registry is seeded with literals rather than an import of
``app.infrastructure.llm.agent.KNOWN_PROVIDERS``: a migration must keep working
when the model code it was generated from has since moved. ``kind`` splits the 6
known providers into the 5 external ones (which hold API keys) and local
``ollama``; the store reads it live to answer ``keys_status()``. Seeding is
deliberately NOT inside the create_table guard -- the table may exist but be
empty -- and uses INSERT OR IGNORE so an existing key row is never overwritten.

No row is seeded into ``llm_active_settings``: the store writes the singleton
lazily on first save, so a first read before any write stays a "no settings yet"
answer rather than a missing-row error.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_llm_config_tables"
down_revision: str | None = "0006_conversation_matter_optional"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors KNOWN_PROVIDERS (app.infrastructure.llm.agent) = EXTERNAL_PROVIDERS |
# LOCAL_PROVIDERS (app.domain.privacy), frozen at migration time on purpose.
EXTERNAL: tuple[str, ...] = ("anthropic", "google", "groq", "openai", "openrouter")
LOCAL: tuple[str, ...] = ("ollama",)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    now = sa.text("CURRENT_TIMESTAMP")

    if not inspector.has_table("llm_providers"):
        op.create_table(
            "llm_providers",
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
            sa.CheckConstraint("kind IN ('local', 'external')", name="ck_llm_providers_kind"),
            sa.PrimaryKeyConstraint("name"),
        )

    if not inspector.has_table("llm_provider_credentials"):
        op.create_table(
            "llm_provider_credentials",
            sa.Column("provider", sa.Text(), nullable=False),
            sa.Column("api_key", sa.Text(), server_default="", nullable=False),
            sa.Column("base_url", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
            sa.ForeignKeyConstraint(["provider"], ["llm_providers.name"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("provider"),
        )

    if not inspector.has_table("llm_active_settings"):
        op.create_table(
            "llm_active_settings",
            sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
            sa.Column("provider", sa.Text(), nullable=True),
            sa.Column("model", sa.Text(), nullable=True),
            sa.Column("json_imported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
            sa.CheckConstraint("id = 1", name="ck_llm_active_settings_id"),
            sa.PrimaryKeyConstraint("id"),
        )

    registry = op.get_bind()
    for name in EXTERNAL:
        registry.execute(
            sa.text("INSERT OR IGNORE INTO llm_providers (name, kind) VALUES (:n, 'external')"),
            {"n": name},
        )
    for name in LOCAL:
        registry.execute(
            sa.text("INSERT OR IGNORE INTO llm_providers (name, kind) VALUES (:n, 'local')"),
            {"n": name},
        )


def downgrade() -> None:
    op.drop_table("llm_active_settings")
    op.drop_table("llm_provider_credentials")
    op.drop_table("llm_providers")
