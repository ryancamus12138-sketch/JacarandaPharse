"""备注的对外格式。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    """备注内容，例如"这张要问财务"。"""
    field_name: str | None = Field(default=None, max_length=80)
    """挂在哪个字段上（字段 ID；表格里的一格是 items.2.amount）。不填 = 整份文档。"""
    page: int | None = Field(default=None, ge=1)
    """字段在第几页。写在字段上时要给（多页文档里不同页可能有同名字段）。"""


class CommentOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    page: int | None
    field_name: str | None
    body: str
    author: str
    """写的人（昵称，没有昵称是用户名）。"""
    mine: bool
    """是不是自己写的（自己写的才能删，管理员都能删）。"""
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    """谁标的已解决。"""
