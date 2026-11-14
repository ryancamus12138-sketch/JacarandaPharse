"""Layout and table structure models. Both are local ONNX models; no network access.

- Layout: pp_layout_cdla bundled with rapid-layout (PaddleOCR PicoDet trained on the Chinese
  document layout dataset CDLA, Apache-2.0), detecting ten region types: text, title, figure,
  figure caption, table, table caption, header, footer, reference, equation. rapid-layout also
  bundles YOLOv8 layout models whose weights are AGPL and cannot be used; only pp_layout_cdla is
  used here.
- Tables: rapid-table + SLANet-plus (PaddleX, Apache-2.0). Given a table image, it recognizes the
  row and column of each cell and its spans. The model file goes in models/table/slanet-plus.onnx
  (downloaded once by the user; see the documentation); without it, cells are derived from text
  positions by rules (analyze.py).

Both libraries download models from the internet by default when they are missing; here model
paths are fixed to local files, and a missing model is simply not used. No network access ever."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from jacara.core.i18n import N_
from jacara.structure.graph import Box

LAYOUT_KINDS = {
    "text": N_("正文"),
    "title": N_("标题"),
    "figure": N_("图"),
    "figure_caption": N_("图注"),
    "table": N_("表格"),
    "table_caption": N_("表注"),
    "header": N_("页眉"),
    "footer": N_("页脚"),
    "reference": N_("参考文献"),
    "equation": N_("公式"),
}
"""Layout model class → display name."""

TABLE = N_("表格")


@dataclass(frozen=True)
class Region:
    """A region detected by the layout model."""

    kind: str
    box: Box
    score: float


@dataclass(frozen=True)
class CellGuess:
    """A cell recognized by the table model: rows row–row_end, columns col–col_end (0-based); box is
    the box of the cell content."""

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
    """Models for page structure analysis. Either may be None: without a layout model no regions or
    tables are detected; without a table model, cells of detected tables are derived from text
    positions."""

    layout: LayoutDetector | None = None
    table: TableRecognizer | None = None


NO_MODELS = StructureEngines()
"""No models at all (tests and trial runs rely on rules only)."""


def _quiet() -> None:
    """Both libraries log many INFO messages on every call; keep only warnings."""
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
    """Model box: (x0, y0, x1, y1) or 8 numbers for four corners, both normalized to (x0, y0, x1,
    y1)."""
    flat = [float(v) for v in np.asarray(values).reshape(-1)]
    xs, ys = flat[0::2], flat[1::2]
    return (min(xs), min(ys), max(xs), max(ys))


@lru_cache
def default_engines() -> StructureEngines:
    """Models for background processing and the CLI: the layout model (unless disabled with
    JACARA_LAYOUT) + the table model (if its file exists)."""
    from jacara.core.config import get_settings

    settings = get_settings()
    table_model = settings.table_model()
    return StructureEngines(
        layout=RapidLayoutDetector() if settings.jacara_layout else None,
        table=RapidTableRecognizer(table_model) if table_model else None,
    )
