"""OCR 样本和团队设置两张表（ADR 0050）。

ocr_samples    一条样本：一张单行小图（存在对象存储）+ 图上原样的文字 + OCR 当时读成什么。
team_settings  按团队存的开关，现在只有"自动收集纠错样本"。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
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
