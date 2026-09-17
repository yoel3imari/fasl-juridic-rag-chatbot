"""Draft HTTP schemas (task 9): request/response models for draft creation.

Response shape (contract for the task-10 frontend; field names stable):
{
  "matter_id": int,
  "draft_id": int,
  "draft_type": "opinion" | "client_email" | "demand_letter" | "memo",
  "content": str,            # opens with the provisional banner line
  "review_state": "draft" | "acknowledged" | "lawyer_reviewed",
  "provisional_banner": str,    # EN exact: "AI-generated draft, not legal
                                # advice, verify before use"
  "provisional_banner_ar": str, # Arabic equivalent
  "provisional_banner_fr": str, # French equivalent
  "status_label": str,       # "Draft — ...", "Acknowledged by user — ..."
                             # or "Reviewed by {reviewer} — ...". NEVER
                             # renders the string "lawyer review".
  "citations": [             # grounded refs only, never fabricated
    {"domain": "matter", "document_id": int, "page": int, "span": [int, int]},
    {"domain": "authority", "source": str, "version": str, "edition": str,
     "article_or_section": str, "pub_date": str?, "doc_date": str?,
     "language": str?}
  ],
  "reviewer": str | None,    # set only on the lawyer_reviewed path
  "reviewed_at": str | None, # ISO timestamp, set only on lawyer_reviewed
  "polished": bool           # True only if optional LLM polish succeeded
}
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DraftType = Literal["opinion", "client_email", "demand_letter", "memo"]


class DraftCreateIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    draft_type: str
    polish: bool = False
    consent: bool = False


class LawyerReviewIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    reviewer: str = Field(min_length=1)


class TransitionIn(BaseModel):
    model_config = ConfigDict(frozen=True)

    to_state: str
    reviewer: str | None = None


class DraftOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    matter_id: int
    draft_id: int
    draft_type: str
    content: str
    review_state: str
    provisional_banner: str
    provisional_banner_ar: str
    provisional_banner_fr: str
    status_label: str
    citations: list[dict[str, Any]]
    reviewer: str | None
    reviewed_at: datetime | None
    polished: bool
