"""字段值和人工审核记录的表。

extracted_values   模型抽出的每个字段：值、出处、对齐状态。写入后永远不改（项目规则 2）。
value_revisions    人工审核：每次修改或确认都新增一条，不修改、不删除（项目规则 2、3）。

当前值 = 最新一条审核记录里的值；一条记录都没有，就是模型的原值。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
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
    """模型抽出的原值。值统一存成文字（数字也存成 "9.0"），按 type 还原；None 表示原文没有。"""
    row_no: Mapped[int | None]
    """模型说的行号（不叫 row：row 在 SQL 里是关键字）。"""

    status: Mapped[str] = mapped_column(String(16))
    """对齐结果：matched、fuzzy、not_found、empty。"""
    needs_review: Mapped[bool]
    """抽取时判断：这个值要不要人工确认。写入后不改；有没有人处理过看 reviewed。"""
    note: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any] | None]
    """出处：行号、行文字、坐标框、OCR 置信度、相似度。"""

    extraction: Mapped[Extraction] = relationship(back_populates="values")
    revisions: Mapped[list[ValueRevision]] = relationship(
        back_populates="original", order_by="ValueRevision.seq"
    )

    # 下面三个不是表里的列，是根据审核记录算出来的。用之前要先把 revisions 查出来。

    @property
    def current_value(self) -> str | None:
        """当前值：最新一条审核记录里的值；没人审核过就是原值。"""
        return self.revisions[-1].new_value if self.revisions else self.value

    @property
    def reviewed(self) -> bool:
        """有人修改或确认过。"""
        return bool(self.revisions)

    @property
    def pending_review(self) -> bool:
        """还在等人确认：抽取时标了需要确认，而且还没人处理。"""
        return self.needs_review and not self.reviewed


class ValueRevision(Base, OwnedMixin, TimestampMixin):
    """一次人工审核：修改（edit）或确认无误（confirm）。只新增，不修改也不删除。

    user_id（来自 OwnedMixin）记录是谁做的，created_at 记录什么时候。
    """

    __tablename__ = "value_revisions"
    __table_args__ = (UniqueConstraint("value_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    value_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extracted_values.id"), index=True)
    seq: Mapped[int] = mapped_column(server_default="1")
    """同一个字段的第几次审核：1、2、3……两个人同时改同一个字段，只有一个能成功。"""
    action: Mapped[str] = mapped_column(String(16), server_default="edit")
    """edit（修改）或 confirm（确认原来的值是对的）。"""
    new_value: Mapped[str | None] = mapped_column(Text)
    """这次审核之后的值。确认时就是确认的那个值，这样每条记录单独看也完整。"""
    reason: Mapped[str] = mapped_column(Text, default="")

    original: Mapped[ExtractedValue] = relationship(back_populates="revisions")
