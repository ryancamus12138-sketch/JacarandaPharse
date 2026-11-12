"""字段值和审核记录的对外格式。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SuggestionOut(BaseModel):
    """沿用上一轮的审核：confirm 确认这次的值；edit 改成上次人改的值。"""

    action: Literal["confirm", "edit"]
    value: str | None
    run: int
    """来自第几轮。"""


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
    """抽取时找到的出处，永远不变。"""
    current_evidence: dict[str, Any] | None = None
    """当前出处：从原文选取过就是选的那几块，否则等于 evidence。原图上画的是这个。"""
    origin: str = "model"
    """model：模型抽的；manual：审核时人工增加的行。"""
    deleted: bool = False
    """这一格所在的行被删除了（表格才有）。"""
    evidence_state: str = "empty"
    """证据（ADR 0028）：verified 原文一致、pending 有疑点、missing 找不到、empty 原文没有。"""
    source_value: str | None = None
    """原文值：按出处文字得到的值，和当前值不一样时才有（"采用原文值"）。只有文档详情里有。"""
    check_state: str | None = None
    """校验：ok 没问题、warn 违反警告级规则、fail 违反错误级规则。按当前值算，只有文档详情里有。"""
    level: str | None = None
    """颜色：red、yellow、grey、green（证据 × 校验 × 有没有人审核过）。只有文档详情里有。"""
    label: str | None = None
    """中文名：模板里的 label；键值对发现时是单据上的原名。只有文档详情里有。"""
    suggestion: SuggestionOut | None = None
    """重新处理之后：上一轮人审核过、可以直接沿用的结果。只有文档详情里有。"""


class AdoptOut(BaseModel):
    confirmed: int
    edited: int


class RevisionIn(BaseModel):
    """一次审核。修改时给出新值和原因；确认时只要 action，原因可以不写。"""

    action: Literal["edit", "confirm"]
    """edit：改成 value；confirm：当前值是对的。"""
    value: str | None = None
    """新值。只有 edit 用；null 表示"原文里确实没有这个字段"。
    给了 source_lines 又没给 value 时，从选的文字里读出值。"""
    reason: str = Field(default="", max_length=500)
    """为什么这样改。修改时必须写，以后查记录时能看懂（从原文选取时可以不写）。"""
    source_lines: list[int] | None = Field(default=None, max_length=50)
    """从原文选取：选了这一页 OCR 结果里的第几个文字块（从 0 开始，见 /pages/{n}/ocr）。
    新出处就是这几块，值从它们的文字读出。"""
    source_box: list[float] | None = Field(default=None, min_length=4, max_length=4)
    """框选：人在原图上框出的区域 [x0, y0, x1, y1]（重新识别这块之后确认的值放在 value）。
    新出处就是这个框。"""


class ConfirmManyIn(BaseModel):
    """一次确认好几个字段（例如商品明细一整行、或者全部标黄的）。"""

    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    reason: str = Field(default="", max_length=500)


class ConfirmManyOut(BaseModel):
    confirmed: int
    """这次确认了几个。已经删掉的行会跳过。"""
    skipped: int


class RowIn(BaseModel):
    """表格增加一行。after 是插在哪一行后面（行的编号，见字段名 items.3.amount 里的 3）；
    0 表示插在最前面；不写就加在最后。"""

    after: int | None = None
    reason: str = Field(default="", max_length=500)


class RowChange(BaseModel):
    """删除或恢复一行。原因必填。"""

    reason: str = Field(default="", max_length=500)


class RevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    action: str
    new_value: str | None
    reason: str
    evidence: dict[str, Any] | None = None
    """从原文选取时的新出处。"""
    user_id: uuid.UUID
    """谁做的。个人模式下都是同一个人；团队模式下就能看出是谁改的。"""
    created_at: datetime


class ValueHistory(BaseModel):
    """一个字段的完整经历：模型原值 + 每一次审核。"""

    value: ValueOut
    revisions: list[RevisionOut]
