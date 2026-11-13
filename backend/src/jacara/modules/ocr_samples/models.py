"""OCR 样本和团队设置两张表（ADR 0050）。

ocr_samples          一条样本：一张单行小图（存在对象存储）+ 图上原样的文字 + OCR 当时读成什么。
team_settings        按团队存的设置："自动收集纠错样本"开关、划分用的种子和版本。
doc_group_members    业务单据（文件 + 这张单据的起始页）属于哪一组（ADR 0052）。
split_assignments    每一组在某个划分版本里是训练、验证、锁定测试还是隔离。
doc_pair_decisions   "这两张是不是同一张单据"人确认过的结果（PDF 和照片配对）。
ocr_sample_exports   每次导出了哪些样本、哪个修订、分在哪边；以后发现重复时在这里记一笔。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class TeamSetting(Base, TimestampMixin):
    __tablename__ = "team_settings"

    team_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    """设置的名字，例如 collect_ocr_samples。"""
    value: Mapped[Any] = mapped_column(JsonColumn)


class OcrSample(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "ocr_samples"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    value_id: Mapped[uuid.UUID | None]
    """从哪个字段的审核来的。"""
    revision_id: Mapped[uuid.UUID | None]
    """哪一条审核记录（保存审核时收集的）。"""
    page: Mapped[int]
    """文件的第几页（从 1 开始）。"""
    unit_page: Mapped[int] = mapped_column(default=1)
    """这张单据在文件里从第几页开始（ADR 0052）：和 document_id 一起认出是哪一张业务单据。"""
    revision: Mapped[int] = mapped_column(default=1)
    """图中原文改了几次（从 1 开始），导出清单里记着，知道训练用的是哪一版标签。"""
    kind: Mapped[str] = mapped_column(String(8))
    """line：点的是一块 OCR 原文，按它的四个角裁；box：框选，按框裁。"""
    polygon: Mapped[list[Any]] = mapped_column(JsonColumn)
    """裁图用的四个角 [[x, y] × 4]（页面图上的像素，PDF 是 216 DPI 的图）。框选的是框的四个角。"""
    transcription: Mapped[str] = mapped_column(Text)
    """图中原文：图上原样的文字，训练标签。"""
    ocr_text: Mapped[str | None] = mapped_column(Text)
    """OCR 当时读成什么。框里原来没有 OCR 文字块是空（None）。"""
    category: Mapped[str] = mapped_column(String(16))
    """rec_error（认错字）、correct（认对的行）、missed（漏认）、unclear（原因未确定）。"""
    error_tags: Mapped[list[Any]] = mapped_column(JsonColumn, default=list)
    template: Mapped[str] = mapped_column(String(200), default="")
    """模板的名字（导出时用），例如"收据"。"""
    field: Mapped[str] = mapped_column(String(200), default="")
    """字段 ID（表格的格子去掉行号，items.amount）。"""
    field_label: Mapped[str] = mapped_column(String(200), default="")
    source_format: Mapped[str] = mapped_column(String(8))
    """pdf 或 image。"""
    ocr_engine: Mapped[str] = mapped_column(String(128), default="")
    """首轮 OCR 用的引擎和模型版本。"""
    crop_key: Mapped[str | None] = mapped_column(String(255))
    """小图在对象存储里的 key。裁图失败是空。"""
    crop_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    width: Mapped[int | None]
    height: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(12), default="ready")
    """ready（可以导出）、failed（裁图失败，可以重试）、withdrawn（撤回了，不导出）。"""
    error: Mapped[str] = mapped_column(Text, default="")
    """裁图失败的原因。"""


class DocGroupMember(Base, OwnedMixin, TimestampMixin):
    """一个业务单据属于哪一组（ADR 0052）。同一组的样本永远在同一边（训练或验证）。"""

    __tablename__ = "doc_group_members"
    __table_args__ = (UniqueConstraint("document_id", "first_page"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    group_id: Mapped[uuid.UUID] = mapped_column(index=True)
    """组号：随机 UUID，不含业务信息。PDF 和照片确认是同一张以后共用一个。"""
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    first_page: Mapped[int]
    """这张单据在文件里从第几页开始。"""


class SplitAssignment(Base, TimestampMixin):
    """一组在某个划分版本里分在哪边（ADR 0052）。第一次进数据集时按哈希分好存下，之后只读这里。"""

    __tablename__ = "split_assignments"

    team_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    split: Mapped[str] = mapped_column(String(16))
    """train（训练）、val（验证）、test_locked（锁定测试集）、quarantined（隔离，不导出）。"""
    reason: Mapped[str] = mapped_column(Text, default="")
    """为什么是这边：按哈希分的、管理员改的、合并时发现已导出过所以隔离……"""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PairDecision(Base, OwnedMixin, TimestampMixin):
    """两张业务单据是不是同一张（人确认过的）。same：合并了；different：不是，不再提名。"""

    __tablename__ = "doc_pair_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    a_document: Mapped[uuid.UUID] = mapped_column(index=True)
    a_page: Mapped[int]
    b_document: Mapped[uuid.UUID] = mapped_column(index=True)
    b_page: Mapped[int]
    decision: Mapped[str] = mapped_column(String(12))


class OcrSampleExport(Base, OwnedMixin, TimestampMixin):
    """一次导出（ADR 0052）：导了哪些样本、哪个修订、分在哪边。
    发现跨集合重复时在 affected 里记一笔。"""

    __tablename__ = "ocr_sample_exports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(12))
    """train_val（训练和验证）或 test（锁定测试集）。"""
    split_version: Mapped[int]
    filters: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    counts: Mapped[dict[str, Any]] = mapped_column(JsonColumn)
    """每边几组、几行。"""
    items: Mapped[list[Any]] = mapped_column(JsonColumn)
    """[{sample, revision, group, split}]"""
    affected: Mapped[list[Any]] = mapped_column(JsonColumn, default=list)
    """导出以后发现的问题：[{group, reason, at}]。
    例如这一组后来和另一组合并，可能一份训练、一份验证。"""
