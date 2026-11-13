"""统计和数据导出的接口数据（ADR 0044）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


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


class CorrectionStats(BaseModel):
    documents: int
    """审核完成的单据数。"""
    fields: int
    edited: int
    confirmed: int
    silent: int
    rate: float
    """人工修改率 = edited / fields。"""
    kinds: list[KindCount]
    templates: list[TemplateStat]
    by_field: list[FieldStat]
    recent: list[Correction]
