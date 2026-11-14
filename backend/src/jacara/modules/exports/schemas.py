"""Public schemas of the export history."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExportedDocument(BaseModel):
    id: uuid.UUID
    filename: str


class ExportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    format: str
    size_bytes: int
    document_count: int
    documents: list[ExportedDocument]
    created_at: datetime
    user_name: str = ""
    """Exporting user (display name, otherwise user name). "Local user" in personal mode."""
    available: bool = True
    """The file is still available for download."""


class ExportPage(BaseModel):
    items: list[ExportOut]
    total: int
