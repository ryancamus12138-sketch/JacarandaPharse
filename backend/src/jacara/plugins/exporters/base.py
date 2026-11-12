"""导出扩展点：把处理结果写成别人能直接用的文件（Excel、CSV……）。

导出器只接收已经处理好的结果，不做识别、抽取或判断，只负责"写成什么样"。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.extractors.base import ExtractionResult
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.validators.base import ValidationResult


@dataclass(frozen=True)
class RangeSetting:
    """日期范围字段导出成什么样（ADR 0039）。"""

    export: str = "normalized"
    """normalized 统一格式、original 原文、columns 拆成开始和结束两列。"""
    separator: str = " ~ "
    day_first: bool | None = None
    """数字日期是不是 日/月（按模板的国家）。"""


@dataclass(frozen=True)
class Review:
    """人工审核的结果（最新一次）。导出时显示这个值，而不是模型的原值。"""

    action: str
    """edit（修改）或 confirm（确认原值是对的）。"""
    value: str | None
    """审核之后的值（文字形式，和数据库里一样）。"""
    reason: str
    reviewed_at: datetime


if TYPE_CHECKING:
    from jacara.structure.graph import PageStructure


@dataclass(frozen=True)
class DocumentReport:
    """一张单据走完整条流水线后的全部结果，导出器只看这个。"""

    source: str
    """原始文件名，例如 receipt.jpg。"""

    extraction: ExtractionResult
    aligned: tuple[AlignedField, ...]
    checks: ValidationResult

    ocr_engine: str
    """OCR 引擎和版本，例如 rapidocr 3.10.0。"""

    processed_at: datetime

    page: int = 1
    """这张单据从第几页开始，从 1 开始。"""

    last_page: int | None = None
    """这张单据到第几页结束（ADR 0036）。只占一页是 None。"""

    pages: tuple[OcrPage, ...] = ()
    """几页拼成的单据：每一页自己的 OCR 结果（存的时候一页一页存）。
    只占一页时是空的，用 ocr_page。"""

    page_starts: tuple[float, ...] = ()
    """几页拼成的单据：每一页在拼成的长页上从多高开始，存出处时换回每一页自己的坐标。"""

    text_source: str = "ocr"
    """这一页的文字怎么来的：ocr（OCR 识别）或 pdf-text（PDF 文字层）。"""

    template: str = "receipt"
    """用的模板名（templates/*.toml）。"""

    template_version: int = 1
    """模板的版本号。模板改过之后，旧结果还能知道是按哪一版抽的。"""

    reviews: Mapping[str, Review] = field(default_factory=dict)
    """字段名 → 人工审核结果。刚处理完、还没人审核时是空的（命令行导出就是这样）。"""

    ocr_page: OcrPage | None = None
    """这一页的 OCR 结果。流水线刚跑完时有，存进数据库给审核页面"点原文填值"用；导出器不用。"""

    structure: PageStructure | None = None
    """这一页的结构（文字块编号、版面区域、表格、标题 → 值，ADR 0034）。存进数据库；导出器不用。"""

    labels: Mapping[str, str] = field(default_factory=dict)
    """字段 ID → 中文名（模板里的 label），导出的列名用它。表格的列写成 items.amount。
    没有的（命令行、老数据）就用字段 ID。"""

    manual: frozenset[str] = frozenset()
    """人工填的字段（模板里设成不让模型抽的），存的时候记成 origin = manual。"""
    computed: frozenset[str] = frozenset()
    """按公式算出来的字段（ADR 0035），存的时候记成 origin = computed。"""

    hidden: frozenset[str] = frozenset()
    """模板里设成"不导出"的字段、表（items）和表的列（items.tax），导出时整列不写。"""

    ranges: Mapping[str, RangeSetting] = field(default_factory=dict)
    """日期范围字段（ADR 0039）：字段 ID（表格的列写成 items.period）→ 导出设置。"""

    template_version_id: uuid.UUID | None = None
    """自动判断选中的"我的模板"版本（ADR 0038）。上传时直接选了模板的是 None（用文档上的）。"""
    classified: Mapping[str, Any] | None = None
    """自动判断的经过（ADR 0038）：选了哪个、为什么、候选有哪些。没有自动判断是 None。"""

    @property
    def pages_label(self) -> str:
        """ "第 3 页"或"第 3–4 页"。"""
        last = self.last_page or self.page
        return f"第 {self.page} 页" if last == self.page else f"第 {self.page}–{last} 页"


class Exporter(Protocol):
    """导出器接口：任何类只要有 name、suffix 属性和 export 方法，就是一个导出器。"""

    name: str
    suffix: str
    """文件后缀，例如 .xlsx。"""

    def export(self, reports: Sequence[DocumentReport], path: Path) -> None:
        """把一张或多张单据的结果写进 path。"""
        ...
