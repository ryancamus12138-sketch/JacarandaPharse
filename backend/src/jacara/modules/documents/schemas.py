"""文档接口的对外格式（API 收什么、返回什么）。

和 models.py 分开：数据库里有的列，不一定都要给前端看（例如 storage_key、user_id）；
前端要的东西，也不一定是数据库里的一列（例如 review_count 是算出来的）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jacara.modules.fields.schemas import ValueOut

__all__ = [
    "Dashboard",
    "DocumentDetail",
    "DocumentNeighbors",
    "DocumentOut",
    "DocumentPage",
    "ExtractionOut",
    "ValueOut",
]
"""ValueOut 搬到了 fields 模块；这里继续导出，原来从这里 import 的代码不用改。"""


class FolderOut(BaseModel):
    """批量导入的一个文件夹。"""

    folder: str
    count: int


class SameFile(BaseModel):
    """内容一样的另一份文档。"""

    id: uuid.UUID
    filename: str
    created_at: datetime


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
    stage: str | None = None
    """处理到哪一步，例如"抽取第 2 / 3 页"。只有处理中才有。"""
    progress: int | None = None
    """处理进度 0～100。只有处理中才有。"""
    processed_at: datetime | None = None
    """处理完（成功或失败）的时间。"""
    reviewed_at: datetime | None = None
    """完成审核的时间。"""
    current_run: int = 1
    """现在用的是第几轮处理的结果。"""
    discover: bool = False
    """下一次（或这一次）处理不用模板，抽出所有键值对。"""
    skipped_at: datetime | None = None
    """审核时点了"先跳过"的时间；没跳过是 null。"""
    skip_reason: str | None = None
    """跳过的原因（可以没有）。"""
    open_comments: int = 0
    """还没解决的备注有几条。"""
    folder: str | None = None
    """批量导入时所在的文件夹（ADR 0036）。"""
    split: str = "auto"
    """一个文件里有几张单据：auto、one、pages 或页码范围。"""
    same_file: list[SameFile] = []
    """同一个文件（内容完全一样）之前也传过：那几份文档（ADR 0036）。"""
    duplicate_skipped: bool = False
    """批量导入时选了"跳过重复的文件"，这次没有新建，返回的是之前那份。"""


class ExtractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page: int
    """这张单据从第几页开始。"""
    last_page: int | None = None
    """到第几页结束（ADR 0036）；只占一页是 null。"""
    source_notes: list[str] = []
    """读文字时的特别情况，例如"PDF 文字层是乱码，这一页改用 OCR"。"""
    source: str
    model: str
    prompt_version: str
    template: str
    """内置模板的名字、我的模板的 id，或者 discover（没用模板，抽出所有键值对）。"""
    template_version: int
    template_title: str = ""
    """模板的中文名。"""
    run: int = 1
    run_reason: str | None = None
    labels: dict[str, str] = {}
    """字段 ID → 中文名。表格的列写成 items.amount，表名写成 items。"""
    checks: dict[str, Any]
    """抽取时的规则结果，永远不变。"""
    current_checks: dict[str, Any] | None = None
    """按审核之后的当前值重新检查的结果（删掉的行不算）。审核页面显示这个。"""
    values: list[ValueOut]


class RunOut(BaseModel):
    """处理过的一轮。"""

    run: int
    template: str
    template_title: str
    template_version: int
    reason: str | None
    created_at: datetime
    current: bool


class DocumentDetail(DocumentOut):
    """一个文档的全部信息：文档本身 + 每一页的抽取结果。"""

    extractions: list[ExtractionOut]
    runs: list[RunOut] = []
    """处理过的每一轮（新的在前）。只有一轮时也有一项。"""
    viewing_run: int = 1
    """extractions 是第几轮的。不是 current_run 时只能查看，不能审核。"""
    suggestions: int = 0
    """可以沿用上一轮审核的字段有几个（"采纳上次的确认"）。"""


REPROCESS_REASONS: dict[str, str] = {
    "template_changed": "模板改了",
    "wrong_template": "选错了模板",
    "bad_result": "结果不好，再跑一次",
    "model_changed": "换了模型或 OCR",
    "other": "其他",
}
"""重新处理的原因（审计和历史里显示中文）。"""


class ReprocessIn(BaseModel):
    """重新处理：换模板（或用同一个模板的最新版）跑一轮新的。旧的结果留着可以查看。"""

    template: str | None = None
    """我的模板的 id，或者 discover。不写：用现在这个模板的最新版。"""
    reason: Literal["template_changed", "wrong_template", "bad_result", "model_changed", "other"]
    note: str = Field(default="", max_length=500)
    """补充说明。原因选"其他"时必须写。"""
    split: str | None = Field(default=None, max_length=200)
    """文件里有几张单据（ADR 0036）：auto、one、pages 或"1-2, 3, 4-6"。不写：和上次一样。"""


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


class DocumentNeighbors(BaseModel):
    """审核页面的"上一份 / 下一份"：按列表顺序，前后两份待审核的文档。没有就是 null。"""

    previous: uuid.UUID | None
    next: uuid.UUID | None
    remaining: int
    """除了这一份，还有几份待审核。"""


class DashboardCounts(BaseModel):
    queued: int
    """排队中（uploaded）"""
    processing: int
    to_review: int
    """处理完、待审核（done）"""
    failed: int
    reviewed: int
    total: int
    skipped: int = 0
    """待审核里先跳过的有几份。"""
    pending_fields: int
    """待审核的文档里，一共还有多少字段等人确认。"""
    processed_today: int
    reviewed_today: int
    average_page_seconds: float | None
    """最近 7 天每页平均处理用时（秒）。"""


class Dashboard(BaseModel):
    """工作台：统计卡、处理队列、需要先处理。"""

    counts: DashboardCounts
    queue: list[DocumentOut]
    """排队中和处理中的（处理中的在前，带进度）。"""
    failed: list[DocumentOut]
    """处理失败的（最新的几份）。"""
    to_review: list[DocumentOut]
    """待审核里等人确认的字段最多的几份（review_count）。"""


class SkipIn(BaseModel):
    """先跳过这份：拿不准的先放一放，过后再回来。"""

    reason: str = Field(default="", max_length=500)
    """为什么跳过，可以不填。"""


class StructureRegion(BaseModel):
    kind: str
    """标题、正文、表格、页眉、页脚……"""
    box: list[float]


class StructureCell(BaseModel):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    box: list[float]
    text: str


class StructureTable(BaseModel):
    box: list[float]
    rows: int
    cols: int
    by: str
    """model：表格结构模型认的；rule：按文字位置划分的。"""
    cells: list[StructureCell]


class StructurePair(BaseModel):
    key: str
    key_box: list[float]
    value: str
    value_box: list[float]
    rule: str
    """哪条规则找出来的：inline、colon、colon_below、header_above、two_column_table、label_left。"""


class PageStructureOut(BaseModel):
    """某一页的结构（ADR 0034）：审核页"版面结构"在原图上画出来，看系统认出了什么。"""

    available: bool
    """2026-11-14 之前处理的没有结构。"""
    sources: dict[str, str] = {}
    regions: list[StructureRegion] = []
    tables: list[StructureTable] = []
    pairs: list[StructurePair] = []
