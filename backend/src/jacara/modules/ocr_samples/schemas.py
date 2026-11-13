"""OCR 样本接口的数据格式（ADR 0050）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["rec_error", "correct", "missed", "unclear"]
Status = Literal["ready", "failed", "withdrawn"]
SourceFormat = Literal["pdf", "image"]


class SampleSettings(BaseModel):
    collect: bool
    """自动收集纠错样本开着没有。"""
    can_change: bool = False
    """现在这个人能不能改开关（管理员）。"""


class SampleSettingsIn(BaseModel):
    collect: bool


class SampleOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    document_id: uuid.UUID
    filename: str
    page: int
    kind: str
    transcription: str
    ocr_text: str | None
    category: Category
    error_tags: list[str]
    template: str
    field: str
    field_label: str
    source_format: SourceFormat
    ocr_engine: str
    width: int | None
    height: int | None
    status: Status
    error: str


class SampleList(BaseModel):
    items: list[SampleOut]
    total: int
    """按筛选条件一共有几条（分页之前）。"""
    categories: dict[str, int]
    """没撤回的样本里每种分类各有几条（不受筛选影响）。"""
    statuses: dict[str, int]
    """每种状态各有几条（不受筛选影响）。"""


class SampleIn(BaseModel):
    """样本页改一条：图中原文、分类、撤回（withdrawn）或恢复（ready）。不改的不传。"""

    transcription: str | None = Field(default=None, max_length=500)
    category: Category | None = None
    status: Literal["ready", "withdrawn"] | None = None


class Excluded(BaseModel):
    reason: str
    count: int


class ExportPreview(BaseModel):
    included: int
    """会导出几条。"""
    categories: dict[str, int]
    """导出的里面每种分类各有几条。"""
    excluded: list[Excluded]
    """不导出的，按原因。"""
