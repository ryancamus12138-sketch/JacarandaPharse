"""统计和数据导出的接口数据（ADR 0044）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class KindCount(BaseModel):
    kind: str
    name: str
    """中文名：小数点、日月颠倒……"""
    count: int


class FieldStat(BaseModel):
    """一个模板的一个字段（表格的列合在一起算，例如"商品明细 · 金额"）。"""

    template: str
    template_title: str
    field: str
    label: str
    total: int
    """审核过的单据里，这个字段一共出现了几次。"""
    edited: int
    """人改了几次（值和模型给的不一样）。"""
    silent: int
    """改了，但抽取时没标黄（错了却没提醒）。"""
    rate: float
    kinds: list[KindCount]


class TemplateStat(BaseModel):
    template: str
    template_title: str
    documents: int
    fields: int
    edited: int
    silent: int
    rate: float


class Correction(BaseModel):
    """最近的一次人工修改。敏感字段不显示值。"""

    document_id: uuid.UUID
    filename: str
    template_title: str
    label: str
    before: str | None
    after: str | None
    kind: str
    kind_name: str
    flagged: bool
    """抽取时标黄了没有。"""
    reason: str
    at: datetime
    attribution_id: uuid.UUID | None = None
    stage: str | None = None
    """错在哪一步（ADR 0053）：人改判过的用人改的。"""
    stage_name: str = ""
    stage_reason: str = ""
    """为什么这么判断（一句话）。"""
    overridden: bool = False
    """人改判过。"""
    auto_stage: str | None = None
    """自动判断的阶段（改判过的话和 stage 不一样）。"""
    evidence: list[dict[str, Any]] = []
    """证据（敏感字段不给）。"""


class StageCount(BaseModel):
    stage: str
    name: str
    count: int


class AttributionIn(BaseModel):
    """人改判：stage 是 null 表示撤销改判，用回自动判断的。"""

    stage: str | None
    note: str = Field(default="", max_length=500)


class AttributionOut(BaseModel):
    id: uuid.UUID
    stage: str
    auto_stage: str
    overridden: bool
    note: str


class CorrectionStats(BaseModel):
    documents: int
    """审核完成的单据数。"""
    fields: int
    edited: int
    confirmed: int
    silent: int
    rate: float
    """人工修改率 = edited / fields。"""
    review_seconds: float | None = None
    """平均每份审核用了几秒（只算记了用时的单据；审核页开着、人在操作的时间）。"""
    timed_documents: int = 0
    """记了审核用时的有几份。"""
    kinds: list[KindCount]
    stages: list[StageCount] = []
    """改过的字段错在哪一步（ADR 0053）。"""
    templates: list[TemplateStat]
    by_field: list[FieldStat]
    recent: list[Correction]
