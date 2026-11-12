"""备注表。

comments  审核时留给同事的话，挂在整份文档上（field_name 为空）或某个字段上。
          可以标"已解决"；只有写的人或管理员能删。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from jacara.core.db import Base, OwnedMixin, TimestampMixin, new_id


class Comment(Base, OwnedMixin, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    page: Mapped[int | None]
    """挂在第几页的字段上（多页文档里不同页可能有同名字段）。整份文档的备注为空。"""
    field_name: Mapped[str | None] = mapped_column(String(80))
    """挂在哪个字段上（字段 ID，例如 total；表格里的一格是 items.2.amount）。
    空 = 整份文档。"""
    body: Mapped[str] = mapped_column(Text)
    """备注内容。"""
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """标成"已解决"的时间。"""
    resolved_by: Mapped[uuid.UUID | None]
    """谁标的已解决。"""
