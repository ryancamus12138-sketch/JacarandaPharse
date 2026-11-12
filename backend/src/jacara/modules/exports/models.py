"""导出记录的表。

exports   一次导出：谁、什么时候、导出了哪些文档、什么格式；文件本身存在对象存储里。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, JsonColumn, OwnedMixin, TimestampMixin, new_id


class Export(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "exports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    """下载时的文件名，例如 jacara-20261112-3份.xlsx。"""
    format: Mapped[str] = mapped_column(String(16))
    """xlsx（汇总在一个 Excel）或 zip（每份一个 Excel）。"""
    size_bytes: Mapped[int]
    document_count: Mapped[int]
    documents: Mapped[list[Any]] = mapped_column(JsonColumn)
    """导出了哪些文档：[{"id", "filename"}]。文件名记下来，文档以后改名或删除也看得出是哪份。"""
    storage_key: Mapped[str | None] = mapped_column(String(512))
    """文件在对象存储里的位置。存的时候失败了就是空（文件已经给了用户，只是不能重新下载）。"""
