"""错误归因（ADR 0053）：人改过的字段，错在哪一步、凭什么这么说。

error_attributions  一个改过的字段一条：自动判断的阶段、原因代码、证据、判断规则的版本；
                    人可以改判（override_*），改判的不会被重新判断覆盖。
"""

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
    """按哪一次修改判断的（又改了一次就重新判断）。"""
    stage: Mapped[str] = mapped_column(String(16))
    """自动判断的阶段：ocr_misread、ocr_uncovered、extraction、matching、template、mixed、unknown。"""
    reason_code: Mapped[str] = mapped_column(String(32))
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JsonColumn)
    """证据：[{"kind": "ocr_sample" | "sent_text" | "revision" | "value" | "template", …}]。"""
    attributor_version: Mapped[str] = mapped_column(String(16))
    override_stage: Mapped[str | None] = mapped_column(String(16))
    """人改判的阶段；没改判是空的。"""
    override_note: Mapped[str] = mapped_column(Text, default="")
    override_by: Mapped[uuid.UUID | None]
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def final_stage(self) -> str:
        return self.override_stage or self.stage
