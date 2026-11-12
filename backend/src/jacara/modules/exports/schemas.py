"""导出记录的对外格式。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExportedDocument(BaseModel):
    id: uuid.UUID
    filename: str


class ExportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    format: str
    size_bytes: int
    document_count: int
    documents: list[ExportedDocument]
    created_at: datetime
    user_name: str = ""
    """谁导出的（显示名，没有就是用户名）。个人模式是"本机用户"。"""
    available: bool = True
    """文件还在，可以重新下载。"""


class ExportPage(BaseModel):
    items: list[ExportOut]
    total: int
