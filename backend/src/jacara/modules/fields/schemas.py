"""字段值和审核记录的对外格式。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ValueOut(BaseModel):
    """一个字段。value 是模型的原值，current_value 是审核之后的当前值。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: str
    value: str | None
    """模型抽出的原值，永远不变。"""
    current_value: str | None = None
    """当前值：最新一次审核的结果；没人审核过就等于 value。界面上显示这个。"""
    status: str
    needs_review: bool
    """抽取时是否标了需要确认（不会变）。"""
    reviewed: bool = False
    """有人修改或确认过。"""
    pending_review: bool = False
    """还在等人确认：needs_review 并且还没人处理。界面上标黄的是这个。"""
    note: str
    evidence: dict[str, Any] | None


class RevisionIn(BaseModel):
    """一次审核。修改时给出新值和原因；确认时只要 action，原因可以不写。"""

    action: Literal["edit", "confirm"]
    """edit：改成 value；confirm：当前值是对的。"""
    value: str | None = None
    """新值。只有 edit 用；null 表示"原文里确实没有这个字段"。"""
    reason: str = Field(default="", max_length=500)
    """为什么这样改。修改时必须写，以后查记录时能看懂。"""


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    action: str
    new_value: str | None
    reason: str
    user_id: uuid.UUID
    """谁做的。个人模式下都是同一个人；团队模式下就能看出是谁改的。"""
    created_at: datetime


class ValueHistory(BaseModel):
    """一个字段的完整经历：模型原值 + 每一次审核。"""

    value: ValueOut
    revisions: list[RevisionOut]
