"""文档接口的对外格式（API 收什么、返回什么）。

和 models.py 分开：数据库里有的列，不一定都要给前端看（例如 storage_key、user_id）；
前端要的东西，也不一定是数据库里的一列（例如 review_count 是算出来的）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jacara.modules.fields.schemas import ValueOut

__all__ = ["DocumentDetail", "DocumentOut", "DocumentPage", "ExtractionOut", "ValueOut"]
"""ValueOut 搬到了 fields 模块；这里继续导出，原来从这里 import 的代码不用改。"""


class DocumentOut(BaseModel):
    """文档列表里的一项。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str | None
    size_bytes: int
    page_count: int
    status: str
    """uploaded（排队中）→ processing（处理中）→ done（完成）/ failed（失败）；
    done 之后人工审核完，点"完成审核"变成 reviewed。"""
    error: str | None = None
    """失败原因。只有 status 是 failed 时才有。"""
    created_at: datetime
    review_count: int = 0
    """还在等人确认的字段数（已经修改或确认过的不算）。"""


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page: int
    source: str
    model: str
    prompt_version: str
    template: str
    template_version: int
    checks: dict[str, Any]
    """抽取时的规则结果，永远不变。"""
    current_checks: dict[str, Any] | None = None
    """按审核之后的当前值重新检查的结果（删掉的行不算）。审核页面显示这个。"""
    values: list[ValueOut]


class DocumentDetail(DocumentOut):
    """一个文档的全部信息：文档本身 + 每一页的抽取结果。"""

    extractions: list[ExtractionOut]


class DocumentPage(BaseModel):
    """分页的列表。total 是总数，前端用来显示"共 N 条"和翻页。"""

    items: list[DocumentOut]
    total: int


class OcrLineOut(BaseModel):
    """原图上的一个文字块。index 是它在这一页里的编号，"从原文选取"时用。"""

    index: int
    text: str
    box: list[float]
    """外接框 [x0, y0, x1, y1]，像素，和页面图片一样的坐标。"""
    polygon: list[list[float]]
    confidence: float
    row: int | None = None
    """在第几个视觉行（和模型看到的行号一样）。"""


class RegionIn(BaseModel):
    """页面上的一块区域：[x0, y0, x1, y1]，和页面图片一样的像素坐标。"""

    box: list[float] = Field(min_length=4, max_length=4)


class RegionOut(BaseModel):
    """重新识别的结果。text 为空表示这块区域里没认出文字。"""

    text: str
    confidence: float | None
    """认出的文字里最低的 OCR 置信度。"""


class PageOcr(BaseModel):
    """一页的 OCR 结果。available=false：这一页处理时还没保存 OCR 结果（重新处理后才有）。"""

    available: bool
    lines: list[OcrLineOut]
