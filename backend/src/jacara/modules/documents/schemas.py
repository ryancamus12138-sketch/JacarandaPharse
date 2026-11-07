"""文档接口的对外格式（API 收什么、返回什么）。

和 models.py 分开：数据库里有的列，不一定都要给前端看（例如 storage_key、user_id）；
前端要的东西，也不一定是数据库里的一列（例如 review_count 是算出来的）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    """文档列表里的一项。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    page_count: int
    status: str
    """uploaded（排队中）→ processing（处理中）→ done（完成）/ failed（失败）"""
    error: str | None = None
    """失败原因。只有 status 是 failed 时才有。"""
    created_at: datetime
    review_count: int = 0
    """需要人工确认的字段数。"""


class ValueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    value: str | None
    status: str
    needs_review: bool
    note: str
    evidence: dict[str, Any] | None


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page: int
    source: str
    model: str
    prompt_version: str
    template: str
    checks: dict[str, Any]
    values: list[ValueOut]


class DocumentDetail(DocumentOut):
    """一个文档的全部信息：文档本身 + 每一页的抽取结果。"""

    extractions: list[ExtractionOut]


class DocumentPage(BaseModel):
    """分页的列表。total 是总数，前端用来显示"共 N 条"和翻页。"""

    items: list[DocumentOut]
    total: int
