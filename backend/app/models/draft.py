from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.matter import Matter


class ReviewState(str, enum.Enum):
    DRAFT = "draft"
    ACKNOWLEDGED = "acknowledged"
    LAWYER_REVIEWED = "lawyer_reviewed"


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    matter_id: Mapped[int] = mapped_column(
        ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    draft_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    review_state: Mapped[ReviewState] = mapped_column(
        Enum(ReviewState, name="review_state", native_enum=False),
        nullable=False,
        default=ReviewState.DRAFT,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    matter: Mapped[Matter] = relationship("Matter", back_populates="drafts")
