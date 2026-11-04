"""字段值和人工修改的表。

extracted_values   模型抽出的每个字段：值、出处、对齐状态。写入后永远不改（项目规则 2）。
value_revisions    人工修改：另存一条记录，不覆盖原值。以后做审核功能时使用。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id

if TYPE_CHECKING:
    from jacara.modules.documents.models import Extraction


class ExtractedValue(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "extracted_values"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id"), index=True)
    position: Mapped[int]
    """在字段清单里的顺序，显示时按这个排。"""

    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str | None] = mapped_column(Text)
    """值统一存成文字（数字也存成 "9.0"），按 type 还原；None 表示原文没有。"""
    row_no: Mapped[int | None]
    """模型说的行号（不叫 row：row 在 SQL 里是关键字）。"""

    status: Mapped[str] = mapped_column(String(16))
    """对齐结果：matched、fuzzy、not_found、empty。"""
    needs_review: Mapped[bool]
    note: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any] | None]
    """出处：行号、行文字、坐标框、OCR 置信度、相似度。"""

    extraction: Mapped[Extraction] = relationship(back_populates="values")
    revisions: Mapped[list[ValueRevision]] = relationship(
        back_populates="original", order_by="ValueRevision.created_at"
    )


class ValueRevision(Base, OwnedMixin, TimestampMixin):
    """人工修改记录：只新增，不修改也不删除。最新的一条就是当前值。"""

    __tablename__ = "value_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    value_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extracted_values.id"), index=True)
    new_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="")

    original: Mapped[ExtractedValue] = relationship(back_populates="revisions")
