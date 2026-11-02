"""表格识别插件接口（预留，暂无实现）。

OCR 只给出一行一行的文字；表格识别再判断每段文字属于第几行、第几列。
以后接入 RapidTable（或其他本地表格模型）时，在本目录新增一个文件实现 TableEngine 即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from jacara.plugins.ocr.base import Device, OcrPage, Point


@dataclass(frozen=True)
class TableConfig:
    """表格识别配置。model 为 None 时用引擎自带的默认模型。"""

    engine: str = "rapidtable"
    model: Path | None = None
    device: Device = "cpu"
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TableCell:
    """一个格子：第几行第几列（从 0 开始）、文字、在图上的位置。合并格用 span 表示跨几行几列。"""

    row: int
    col: int
    text: str
    polygon: tuple[Point, Point, Point, Point]
    row_span: int = 1
    col_span: int = 1


@dataclass(frozen=True)
class Table:
    """一张表：所有格子、整张表在图上的位置、行数和列数。"""

    cells: tuple[TableCell, ...]
    polygon: tuple[Point, Point, Point, Point]
    n_rows: int
    n_cols: int


@dataclass(frozen=True)
class TableResult:
    """一张图上识别出的所有表格，以及来源信息（引擎、模型、用时）。"""

    tables: tuple[Table, ...]
    engine: str
    models: dict[str, str]
    duration_ms: float


class TableEngine(Protocol):
    """所有表格识别引擎都要满足的接口。

    输入原图和已经做好的 OCR 结果，避免重复做一次 OCR。
    """

    name: str

    def recognize(self, image: Path, ocr: OcrPage) -> TableResult: ...
