"""Relational tables backing LLM config (provider, model, API keys, base URLs).

Replaces the plaintext ``<STORAGE_DIR>/llm_settings.json`` read by
:mod:`app.repositories.settings` with three normalised tables: the provider
registry, one credential row per provider, and the active provider/model
singleton.

Built two ways on purpose -- alembic 0007 in production, and
``Table.create(checkfirst=True)`` by the settings store so LLM settings keep
working before migrations have ever run. ``llm_providers.kind`` is read live:
``keys_status()`` / ``masked_keys()`` select ``kind='external'``, so the column
is the DB-side contract for ``SUPPORTED_KEY_PROVIDERS``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LLMProvider(Base):
    """Registry row per known LLM provider; ``kind`` splits local from external."""

    __tablename__ = "llm_providers"
    __table_args__ = (
        CheckConstraint("kind IN ('local', 'external')", name="ck_llm_providers_kind"),
    )

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LLMProviderCredential(Base):
    """One provider's API key and optional base-URL override (keys stay plaintext)."""

    __tablename__ = "llm_provider_credentials"

    provider: Mapped[str] = mapped_column(
        ForeignKey("llm_providers.name", ondelete="CASCADE"),
        primary_key=True,
    )
    api_key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LLMActiveSettings(Base):
    """Singleton (``CHECK(id=1)``) row: active provider/model and the import marker.

    ``json_imported_at`` is the one-shot marker for the legacy-JSON import: once
    set, a later "clear settings" cannot resurrect the old file.
    """

    __tablename__ = "llm_active_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_llm_active_settings_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    json_imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
