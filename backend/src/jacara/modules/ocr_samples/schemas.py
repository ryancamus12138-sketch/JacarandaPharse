"""OCR 样本接口的数据格式（ADR 0050）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Category = Literal["rec_error", "correct", "missed", "unclear"]
Split = Literal["train", "val", "test_locked", "quarantined"]
ExportKind = Literal["train_val", "test"]
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


class SplitCount(BaseModel):
    groups: int
    """几组（几张独立的业务单据）。"""
    rows: int
    """几行（几条样本）。"""


class Evidence(BaseModel):
    """验证集够不够说明问题（ADR 0052）。"""

    level: Literal["process", "thin", "trend", "none"]
    """process：只能检查流程；thin：偏少；trend：可以看整体趋势；none：这次没有验证集。"""
    message: str


class Distribution(BaseModel):
    """按模板、分类、来源，训练和验证各几行（只报分布，不为凑比例挪组）。"""

    by: Literal["template", "category", "source_format"]
    key: str
    train: int = 0
    val: int = 0
    test: int = 0


class ExportPreview(BaseModel):
    kind: ExportKind = "train_val"
    included: int
    """会导出几条。"""
    categories: dict[str, int]
    """导出的里面每种分类各有几条。"""
    excluded: list[Excluded]
    """不导出的，按原因。"""
    splits: dict[str, SplitCount] = {}
    """train、val（或 test）各几组、几行。"""
    split_version: int = 1
    evidence: Evidence | None = None
    val_category_groups: dict[str, int] = {}
    """验证集里每种分类来自几组（200 行来自 3 张发票，证据还是只有 3 组）。"""
    distributions: list[Distribution] = []


class GroupMemberOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    first_page: int
    source_format: str
    samples: int


class GroupOut(BaseModel):
    group_id: uuid.UUID
    code: str
    """导出清单里的组号（g_ 开头，不含业务信息）。"""
    split: Split
    reason: str
    members: list[GroupMemberOut]
    samples: int
    exported_as: list[str]
    """导出过在哪几边。"""


class GroupList(BaseModel):
    items: list[GroupOut]
    split_version: int
    counts: dict[str, int]
    """每种划分各几组。"""


class SplitIn(BaseModel):
    split: Split


class UnitRef(BaseModel):
    document_id: uuid.UUID
    page: int = Field(ge=1)
    """这张单据在文件里从第几页开始。"""


class UnitOut(BaseModel):
    document_id: uuid.UUID
    filename: str
    page: int
    last_page: int
    template: str
    values: dict[str, str]
    """配对用的几个值：单号、日期、金额、币种、发单方。"""
    group_id: uuid.UUID | None = None
    samples: int = 0


class PairOut(BaseModel):
    a: UnitOut
    b: UnitOut


class PairIn(BaseModel):
    a: UnitRef
    b: UnitRef
    decision: Literal["same", "different"]


class PairResult(BaseModel):
    group_id: uuid.UUID | None
    split: Split | None
    quarantined: bool
    affected_exports: int
    message: str


class ExportRecordOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    kind: ExportKind
    split_version: int
    counts: dict[str, Any]
    filters: dict[str, Any]
    affected: list[dict[str, Any]]
