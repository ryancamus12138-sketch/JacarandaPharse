"""文档和抽取记录的表。

documents     一个上传的文件（图片或 PDF）
extractions   对文档某一页的一次抽取：用的模型、提示词、规则校验结果、模型原始回答

重新处理（ADR 0030）：每处理一次是一"轮"（run），旧的几轮都留着可以查看，
documents.current_run 指向现在用的那一轮；Document.extractions 只有当前这一轮。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, and_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id

if TYPE_CHECKING:
    from jacara.modules.fields.models import ExtractedValue
    from jacara.modules.templates.models import TemplateVersion


class Document(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    """文件内容的指纹：同一个文件传两次，指纹相同，可以发现重复。"""
    size_bytes: Mapped[int]
    page_count: Mapped[int]
    storage_key: Mapped[str | None] = mapped_column(String(512))
    """原始文件在对象存储里的位置。通过 API 上传的文件才有。"""
    content_type: Mapped[str | None] = mapped_column(String(100))
    """文件类型，例如 image/jpeg、application/pdf。"""
    status: Mapped[str] = mapped_column(String(16), default="uploaded", server_default="uploaded")
    """处理状态：uploaded（已上传，排队中）→ processing（处理中）→ done（完成）/ failed（失败）。"""
    error: Mapped[str | None] = mapped_column(Text)
    """处理失败的原因（给人看的说明）。成功或还没处理时为空。"""
    stage: Mapped[str | None] = mapped_column(String(64))
    """处理到哪一步（给人看的），例如"识别文字""抽取第 2 / 3 页"。不在处理中时为空。"""
    progress: Mapped[int | None]
    """处理进度 0～100。不在处理中时为空。"""
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """处理完（成功或失败）的时间。工作台"今天处理了几份"用。"""
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """点"完成审核"的时间。"""
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """审核时点了"先跳过"的时间。跳过的文档不算进"下一份"，取消跳过或完成审核后清空。"""
    skip_reason: Mapped[str | None] = mapped_column(Text)
    """为什么跳过（可以不填）。"""
    skipped_by: Mapped[uuid.UUID | None]
    """谁跳过的。"""

    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_versions.id")
    )
    """下一次处理用哪个模板版本。空：用内置的收据模板（v2 之前上传的文档）。"""
    discover: Mapped[bool] = mapped_column(default=False, server_default="false")
    """下一次处理不用模板，抽出单据上所有的键值对（"用样例建模板"的第一步）。"""
    current_run: Mapped[int] = mapped_column(default=1, server_default="1")
    """现在用的是第几轮处理的结果。"""
    run_reason: Mapped[str | None] = mapped_column(Text)
    """下一轮处理的原因（重新处理时填的），处理完记到这一轮的 extractions 上。"""

    extractions: Mapped[list[Extraction]] = relationship(
        primaryjoin=lambda: and_(
            Document.id == foreign(Extraction.document_id),
            Extraction.run == Document.current_run,
        ),
        order_by="Extraction.page",
        viewonly=True,
    )
    """当前这一轮的抽取结果，每页一条。"""
    all_extractions: Mapped[list[Extraction]] = relationship(
        back_populates="document", order_by="[Extraction.run, Extraction.page]"
    )
    """所有轮的抽取结果（看历史用）。"""


class Extraction(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    page: Mapped[int]
    """第几页，从 1 开始。"""

    # 来源（项目规则 4）
    source: Mapped[str] = mapped_column(String(32))
    """这一页的文字怎么来的：ocr 或 pdf-text。"""
    ocr_engine: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(128))
    backend: Mapped[str] = mapped_column(String(32))
    prompt_version: Mapped[str] = mapped_column(String(32))
    template: Mapped[str] = mapped_column(String(64))
    """用的模板名，例如 receipt（templates/receipt.toml）。"""
    template_version: Mapped[int] = mapped_column(default=1, server_default="1")
    """模板的版本号。v2 之前处理的文档都按 1 记。"""
    duration_ms: Mapped[int]
    run: Mapped[int] = mapped_column(default=1, server_default="1")
    """第几轮处理。"""
    run_reason: Mapped[str | None] = mapped_column(Text)
    """为什么重新处理（第一轮为空）。"""
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("template_versions.id")
    )
    """用的"我的模板"的哪个版本。内置模板和键值对发现时为空，按 template 名字找。"""

    raw_output: Mapped[str] = mapped_column(Text)
    """模型的原始回答，原样保存（项目规则 2）。"""
    checks: Mapped[dict[str, Any]]
    """规则校验结果：通过、跳过、不通过的规则和说明。"""
    ocr: Mapped[dict[str, Any] | None] = mapped_column(
        # none_as_null：没有 OCR 结果存成 SQL 的 NULL（而不是 JSON 的 null），
        # jacara backfill-ocr 才能用 "ocr IS NULL" 找出要补的
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        deferred=True,
    )
    """这一页的 OCR 结果：{"lines": [{"text", "box": [x0, y0, x1, y1], "polygon", "confidence"}]}。
    审核时"点原文填值"用。只在要用时才从数据库读（deferred）。2026-11 之前处理的文档没有。"""

    document: Mapped[Document] = relationship(back_populates="all_extractions")
    template_def: Mapped[TemplateVersion | None] = relationship(lazy="selectin")
    """用的模板版本（template_version_id 指向的那条）。"""
    values: Mapped[list[ExtractedValue]] = relationship(
        back_populates="extraction",
        order_by="[ExtractedValue.sort_key, ExtractedValue.position]",
    )
