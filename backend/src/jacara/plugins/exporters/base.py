"""导出扩展点：把处理结果写成别人能直接用的文件（Excel、CSV……）。

导出器只接收已经处理好的结果，不做识别、抽取或判断，只负责"写成什么样"。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from jacara.plugins.aligners.base import AlignedField
from jacara.plugins.extractors.base import ExtractionResult
from jacara.plugins.validators.base import ValidationResult


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


class Exporter(Protocol):
    """导出器接口：任何类只要有 name、suffix 属性和 export 方法，就是一个导出器。"""

    name: str
    suffix: str
    """文件后缀，例如 .xlsx。"""

    def export(self, reports: Sequence[DocumentReport], path: Path) -> None:
        """把一张或多张单据的结果写进 path。"""
        ...
