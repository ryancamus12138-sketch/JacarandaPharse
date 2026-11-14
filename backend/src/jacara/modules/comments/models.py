"""Comments table.

comments  Notes left for colleagues during review, attached to a whole document (field_name
empty) or to a field. Can be marked resolved; only the author or an administrator can delete
them."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id


class Comment(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    page: Mapped[int | None]
    """Page of the field the comment is attached to (fields with the same name may appear on
    different pages of a multi-page document). Empty for document comments."""
    field_name: Mapped[str | None] = mapped_column(String(80))
    """Field the comment is attached to (field ID such as total; a table cell is items.2.amount).
    Empty means the whole document."""
    body: Mapped[str] = mapped_column(Text)
    """Comment text."""
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Time the comment was marked resolved."""
    resolved_by: Mapped[uuid.UUID | None]
    """User who marked it resolved."""
