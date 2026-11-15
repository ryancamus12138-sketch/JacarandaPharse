"""Table recognition plugin interface (reserved; not implemented yet).

OCR yields text line by line; table recognition then assigns each span to a row and column. To
integrate RapidTable (or another local table model), add a module in this package implementing
TableEngine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from jacara.plugins.ocr.base import Device, OcrPage, Point


@dataclass(frozen=True)
class TableConfig:
    """Table recognition configuration. model None means the engine's bundled default model."""

    engine: str = "rapidtable"
    model: Path | None = None
    device: Device = "cpu"
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TableCell:
    """A cell: row and column (0-based), text, and position on the image. Merged cells use span for
    the number of rows and columns covered."""

    row: int
    col: int
    text: str
    polygon: tuple[Point, Point, Point, Point]
    row_span: int = 1
    col_span: int = 1


@dataclass(frozen=True)
class Table:
    """A table: all cells, the table's position on the image, and the number of rows and columns."""

    cells: tuple[TableCell, ...]
    polygon: tuple[Point, Point, Point, Point]
    n_rows: int
    n_cols: int


@dataclass(frozen=True)
class TableResult:
    """All tables recognized on an image, with provenance (engine, model, duration)."""

    tables: tuple[Table, ...]
    engine: str
    models: dict[str, str]
    duration_ms: float


class TableEngine(Protocol):
    """Interface every table recognition engine must satisfy.

    Takes the original image and the existing OCR result to avoid running OCR again."""

    name: str

    def recognize(self, image: Path, ocr: OcrPage) -> TableResult: ...
