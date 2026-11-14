"""Public schemas for comments."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    """Comment text, for example "Check with finance"."""
    field_name: str | None = Field(default=None, max_length=80)
    """Field the comment is attached to (field ID; a table cell is items.2.amount). Omit for the
    whole document."""
    page: int | None = Field(default=None, ge=1)
    """Page of the field. Required for field comments (fields with the same name may appear on
    different pages of a multi-page document)."""


class CommentOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    page: int | None
    field_name: str | None
    body: str
    author: str
    """Author (display name, otherwise user name)."""
    mine: bool
    """Whether the current user is the author (authors can delete their own comments; administrators
    can delete any)."""
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    """User who marked it resolved."""
