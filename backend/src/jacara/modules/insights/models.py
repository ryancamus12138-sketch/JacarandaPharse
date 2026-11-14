"""Error attribution: for each corrected field, the stage at fault and the evidence.

error_attributions  One row per corrected field: automatically determined stage, reason code,
evidence and rule version. A person may override it (override_*); overrides are never overwritten
by re-attribution."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class ErrorAttribution(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "error_attributions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    value_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extracted_values.id"), unique=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    revision_id: Mapped[uuid.UUID]
    """Revision the attribution is based on (re-attributed when edited again)."""
    stage: Mapped[str] = mapped_column(String(16))
    """Automatically determined stage: ocr_misread, ocr_uncovered, extraction, matching, template,
    mixed, unknown."""
    reason_code: Mapped[str] = mapped_column(String(32))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JsonColumn)
    """Evidence: [{"kind": "ocr_sample" | "sent_text" | "revision" | "value" | "template", …}]."""
    attributor_version: Mapped[str] = mapped_column(String(16))
    override_stage: Mapped[str | None] = mapped_column(String(16))
    """Stage set by a person; empty if not overridden."""
    override_note: Mapped[str] = mapped_column(Text, default="")
    override_by: Mapped[uuid.UUID | None]
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def final_stage(self) -> str:
        return self.override_stage or self.stage
