"""导出扩展点：把处理结果写成别人能直接用的文件（Excel、CSV……）。

导出器只接收已经处理好的结果，不做识别、抽取或判断，只负责"写成什么样"。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.extractors.base import ExtractionResult
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.validators.base import ValidationResult


@dataclass(frozen=True)
class Review:
    """人工审核的结果（最新一次）。导出时显示这个值，而不是模型的原值。"""

    action: str
    """edit（修改）或 confirm（确认原值是对的）。"""
    value: str | None
    """审核之后的值（文字形式，和数据库里一样）。"""
    reason: str
    reviewed_at: datetime


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
    """第几页，从 1 开始。"""

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


class Exporter(Protocol):
    """导出器接口：任何类只要有 name、suffix 属性和 export 方法，就是一个导出器。"""

    name: str
    suffix: str
    """文件后缀，例如 .xlsx。"""

    def export(self, reports: Sequence[DocumentReport], path: Path) -> None:
        """把一张或多张单据的结果写进 path。"""
        ...
