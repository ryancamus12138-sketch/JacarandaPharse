"""字段值和人工审核记录的表。

extracted_values   模型抽出的每个字段：值、出处、对齐状态。写入后永远不改（项目规则 2）。
value_revisions    人工审核：每次修改或确认都新增一条，不修改、不删除（项目规则 2、3）。

当前值 = 最新一条审核记录里的值；一条记录都没有，就是模型的原值。

表格（商品明细）的行可以人工增删（ADR 0027）：
- 删除一行 = 这一行每一格都新增一条 action="delete" 的记录，不真的删；恢复 = 再新增一条 restore。
- 增加一行 = 新增几条 origin="manual" 的字段，值是空的，再用审核记录填值。
- 显示顺序看 sort_key：插在两行中间的新行取两边的中间数，原有的行不用改。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id

if TYPE_CHECKING:
    from jacara.modules.documents.models import Extraction


class ExtractedValue(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "extracted_values"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    extraction_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("extractions.id"), index=True)
    position: Mapped[int]
    """在字段清单里的顺序（抽取时定下）。"""
    sort_key: Mapped[float] = mapped_column(Float, server_default="0")
    """显示顺序，按这个排。抽取时等于 position；人工插入的行取前后两行的中间数。"""
    origin: Mapped[str] = mapped_column(String(16), default="model", server_default="model")
    """model：模型抽的；manual：人工填的（表格新增的行、模板里设成人工填的字段）；
    computed：按模板的公式算出来的（ADR 0035），参与计算的值改了会跟着重算。"""

    name: Mapped[str] = mapped_column(String(64))
    label: Mapped[str | None] = mapped_column(String(200))
    """单据上的原名（键值对发现时，kv.3 对应的"Check-in"）。按模板抽的字段为空，名字看模板。"""
    type: Mapped[str] = mapped_column(String(16))
    value: Mapped[str | None] = mapped_column(Text)
    """模型抽出的原值。值统一存成文字（数字也存成 "9.0"），按 type 还原；None 表示原文没有。"""
    rejected_value: Mapped[str | None] = mapped_column(Text)
    """模型给了值，但它说的文字块里没有、原文别处也找不到，按规则没有填（ADR 0034）：
    模型给的值记在这里，value 是空的。人核对原文后可以一键采用。"""
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

    # 下面几个不是表里的列，是根据审核记录算出来的。用之前要先把 revisions 查出来。

    @property
    def current_value(self) -> str | None:
        """当前值：最新一条审核记录里的值；没人审核过就是原值。"""
        return self.revisions[-1].new_value if self.revisions else self.value

    @property
    def current_evidence(self) -> dict[str, Any] | None:
        """当前出处：最近一次"从原文选取"记下的出处；没有就是抽取时找到的出处。

        只改了值（手打的）的时候，出处还是原来的：值已经和原文对不上了，要靠审核记录说明。
        """
        for revision in reversed(self.revisions):
            if revision.evidence is not None:
                return revision.evidence
        return self.evidence

    @property
    def deleted(self) -> bool:
        """这一格所在的行被删除了（表格才有）。"""
        return bool(self.revisions) and self.revisions[-1].action == "delete"

    @property
    def reviewed(self) -> bool:
        """有人修改或确认过。按公式自动重算（action="compute"）不算人审核过。"""
        return any(r.action != "compute" for r in self.revisions)

    @property
    def pending_review(self) -> bool:
        """还在等人确认：抽取时标了需要确认，而且还没人处理。"""
        return self.needs_review and not self.reviewed

    @property
    def evidence_state(self) -> str:
        """证据这一维（ADR 0028），说的是抽取时找到的出处，审核之后也不变：

        - verified：原文里找到了，和值完全一致，没有任何疑点
        - pending：找到了，但要人看一眼（只是相似、行号不对、OCR 置信度低、正负号不对），
          或者是看原图读的（ADR 0038）
        - missing：原文里找不到这个值，可能是模型编的或改写了（也包括按规则没有填的 rejected）
        - empty：模型说原文没有这个字段（人工新加的格子也是）
        """
        if self.status in ("not_found", "rejected"):
            return "missing"
        if self.status == "computed":
            return "verified"  # 按公式算的：对不对看参与计算的字段和规则
        if self.status == "seen":
            return "pending"  # 看原图读的（ADR 0038）：OCR 里对不上，要人对着原图看一眼
        if self.status == "empty" or self.evidence is None:
            return "empty"
        if self.status == "fuzzy" or self.note:
            return "pending"
        return "verified"


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
    """edit（修改）、confirm（确认原来的值是对的）、delete（删除这一行）、restore（恢复这一行）、
    compute（参与计算的值改了，按公式自动重算，ADR 0035；user_id 是改了那个值的人）。"""
    new_value: Mapped[str | None] = mapped_column(Text)
    """这次审核之后的值。确认时就是确认的那个值，这样每条记录单独看也完整。"""
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any] | None]
    """从原文选取时的新出处（和 extracted_values.evidence 一样的格式）；手打的值没有。"""

    original: Mapped[ExtractedValue] = relationship(back_populates="revisions")
