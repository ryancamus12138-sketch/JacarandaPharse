"""版面模型和表格结构模型（ADR 0034）。都是本地 ONNX 模型，不联网。

- 版面：rapid-layout 自带的 pp_layout_cdla（PaddleOCR 的 PicoDet，在中文文档版面数据集 CDLA 上训练，
  Apache-2.0），找出 正文、标题、图、图注、表格、表注、页眉、页脚、参考文献、公式 十类区域。
  rapid-layout 还带 YOLOv8 的版面模型，那些权重是 AGPL 的，不能用，这里只用 pp_layout_cdla。
- 表格：rapid-table + SLANet-plus（PaddleX，Apache-2.0）。给一张表格的图，
  认出每个格子在第几行第几列、跨几行几列。模型文件放在 models/table/slanet-plus.onnx
  （要自己下载一次，见 docs）；没有就不用，表格的格子按文字位置用规则划分（analyze.py）。

两个库默认会在找不到模型时从网上下载；这里都把模型路径写死成本地文件，找不到就不用，绝不联网。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from jacara.structure.graph import Box

LAYOUT_KINDS = {
    "text": "正文",
    "title": "标题",
    "figure": "图",
    "figure_caption": "图注",
    "table": "表格",
    "table_caption": "表注",
    "header": "页眉",
    "footer": "页脚",
    "reference": "参考文献",
    "equation": "公式",
}
"""版面模型的类别 → 中文名。"""

TABLE = "表格"


@dataclass(frozen=True)
class Region:
    """版面模型找出的一个区域。"""

    kind: str
    box: Box
    score: float


@dataclass(frozen=True)
class CellGuess:
    """表格模型认出的一个格子：第 row～row_end 行、第 col～col_end 列（从 0 开始），
    box 是格子里内容的框。"""

    row: int
    row_end: int
    col: int
    col_end: int
    box: Box


class LayoutDetector(Protocol):
    name: str

    def detect(self, image: np.ndarray) -> list[Region]: ...


class TableRecognizer(Protocol):
    name: str

    def recognize(self, image: np.ndarray) -> list[CellGuess]: ...


@dataclass(frozen=True)
class StructureEngines:
    """分析页面结构用的模型。都可以是 None：没有版面模型就不找区域、不找表格；
    没有表格模型，找到的表格按文字位置划分格子。"""

    layout: LayoutDetector | None = None
    table: TableRecognizer | None = None


NO_MODELS = StructureEngines()
"""不用任何模型（测试、试跑时只靠规则）。"""


def _quiet() -> None:
    """两个库每次都打很多 INFO 日志，只留警告。"""
    for name in ("rapid_layout", "rapid_table"):
        logging.getLogger(name).setLevel(logging.WARNING)
        for child in list(logging.root.manager.loggerDict):
            if child.startswith(name):
                logging.getLogger(child).setLevel(logging.WARNING)


class RapidLayoutDetector:
    name = "pp_layout_cdla"

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold
        self._engine: Any = None

    def detect(self, image: np.ndarray) -> list[Region]:
        if self._engine is None:
            import rapid_layout
            from rapid_layout import RapidLayout

            bundled = Path(rapid_layout.__file__).parent / "models" / "layout_cdla.onnx"
            self._engine = RapidLayout(
                model_type="pp_layout_cdla",
                model_dir_or_path=str(bundled),
                conf_thresh=self.threshold,
            )
            _quiet()
        out = self._engine(image)
        boxes = out.boxes or []
        names = out.class_names or []
        scores = out.scores or []
        return [
            Region(LAYOUT_KINDS.get(str(n), str(n)), _box(b), float(s))
            for b, n, s in zip(boxes, names, scores, strict=False)
        ]


class RapidTableRecognizer:
    def __init__(self, model: Path) -> None:
        self.model = model
        self.name = model.name
        self._engine: Any = None

    def recognize(self, image: np.ndarray) -> list[CellGuess]:
        if self._engine is None:
            from rapid_table import ModelType, RapidTable, RapidTableInput

            lower = self.model.name.lower()
            kind = (
                ModelType.SLANETPLUS
                if "plus" in lower
                else ModelType.PPSTRUCTURE_ZH
                if lower.startswith("ch_")
                else ModelType.PPSTRUCTURE_EN
            )
            self._engine = RapidTable(
                RapidTableInput(model_type=kind, model_dir_or_path=str(self.model), use_ocr=False)
            )
            _quiet()
        out = self._engine(image)
        if not out.logic_points or not out.cell_bboxes:
            return []
        cells: list[CellGuess] = []
        for logic, box in zip(out.logic_points[0], out.cell_bboxes[0], strict=False):
            r0, r1, c0, c1 = (int(v) for v in list(logic)[:4])
            cells.append(CellGuess(r0, r1, c0, c1, _box(box)))
        return cells


def _box(values: Any) -> Box:
    """模型给的框：(x0, y0, x1, y1) 或者四个角的 8 个数，都变成 (x0, y0, x1, y1)。"""
    flat = [float(v) for v in np.asarray(values).reshape(-1)]
    xs, ys = flat[0::2], flat[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


@lru_cache
def default_engines() -> StructureEngines:
    """后台处理和命令行用的模型：版面模型（JACARA_LAYOUT 关掉就不用）+ 表格模型（有文件才用）。"""
    from jacara.core.config import get_settings

    settings = get_settings()
    table_model = settings.table_model()
    return StructureEngines(
        layout=RapidLayoutDetector() if settings.jacara_layout else None,
        table=RapidTableRecognizer(table_model) if table_model else None,
    )
