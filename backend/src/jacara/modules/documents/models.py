"""文档和抽取记录的表。

documents     一个上传的文件（图片或 PDF）
extractions   对文档某一页的一次抽取：用的模型、提示词、规则校验结果、模型原始回答
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id

if TYPE_CHECKING:
    from jacara.modules.fields.models import ExtractedValue


class Document(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    """文件内容的指纹：同一个文件传两次，指纹相同，可以发现重复。"""
    size_bytes: Mapped[int]
    page_count: Mapped[int]
    storage_key: Mapped[str | None] = mapped_column(String(512))
    """原始文件在对象存储里的位置。下一课做上传时填写。"""

    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="document", order_by="Extraction.page"
    )


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
    """用的字段清单，例如 receipt、full。以后有了模板功能，这里是模板名和版本。"""
    duration_ms: Mapped[int]

    raw_output: Mapped[str] = mapped_column(Text)
    """模型的原始回答，原样保存（项目规则 2）。"""
    checks: Mapped[dict[str, Any]]
    """规则校验结果：通过、跳过、不通过的规则和说明。"""

    document: Mapped[Document] = relationship(back_populates="extractions")
    values: Mapped[list[ExtractedValue]] = relationship(
        back_populates="extraction", order_by="ExtractedValue.position"
    )
