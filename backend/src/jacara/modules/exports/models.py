"""Export history table.

exports   One export: who, when, which documents, which format; the file itself is stored in
object storage."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class Export(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "exports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    """Download file name, for example jacara-20261112-3.xlsx."""
    format: Mapped[str] = mapped_column(String(16))
    """xlsx (summary in one workbook) or zip (one workbook per document)."""
    size_bytes: Mapped[int]
    document_count: Mapped[int]
    documents: Mapped[list[Any]] = mapped_column(JsonColumn)
    """Exported documents: [{"id", "filename"}]. File names are recorded so the documents remain
    identifiable after renaming or deletion."""
    storage_key: Mapped[str | None] = mapped_column(String(512))
    """Location of the file in object storage. Empty if storing failed (the user received the file
    but cannot download it again)."""
