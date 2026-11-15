"""Several documents in one file: the pages of one document are stacked into a single page for
processing and split back into pages when stored.

Why stack: extraction, evidence location and layout analysis all operate on one page. Stacking a
two-page hotel bill into one tall page joins line-item tables that span pages naturally, without
changing any later step. When storing, the OCR results and evidence boxes of each page are
converted back to that page's coordinates, so the review page shows the images page by page as
usual."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from jacara.plugins.ocr.base import FormValue, OcrLine, OcrPage, Point
from jacara.structure.engines import LayoutDetector, Region, StructureEngines

if TYPE_CHECKING:
    from jacara.models import Extraction

GAP = 60
"""Gap in pixels between stacked pages: much larger than the line spacing, so the last line of one
page and the first line of the next are never merged."""


@dataclass(frozen=True)
class Stacked:
    """Several pages stacked into one. starts[k] is the y offset at which page k (0-based) begins on
    the tall page."""

    page: OcrPage
    starts: tuple[float, ...]
    pages: tuple[OcrPage, ...]

    def page_index(self, y: float) -> int:
        """Page index (0-based) containing height y on the tall page."""
        index = 0
        for k, start in enumerate(self.starts):
            if y >= start - GAP / 2:
                index = k
        return index


def stack_pages(pages: Sequence[OcrPage]) -> Stacked:
    """Stack pages vertically with GAP between them. A single page is returned unchanged."""
    if len(pages) == 1:
        return Stacked(pages[0], (0.0,), tuple(pages))
    lines: list[OcrLine] = []
    layer: list[OcrLine] = []
    forms: list[FormValue] = []
    starts: list[float] = []
    top = 0.0
    for page in pages:
        starts.append(top)
        lines += [_shift(line, top) for line in page.lines]
        layer += [_shift(line, top) for line in page.text_layer]
        forms += [_shift_form(f, top) for f in page.forms]
        top += page.height + GAP
    engines = sorted({p.engine for p in pages})
    models: dict[str, str] = {}
    for p in pages:
        models.update(p.models)
    return Stacked(
        OcrPage(
            width=max(p.width for p in pages),
            height=round(top - GAP),
            lines=tuple(lines),
            engine=engines[0] if len(engines) == 1 else "mixed",
            models=models,
            duration_ms=sum(p.duration_ms for p in pages),
            text_layer=tuple(layer),
            forms=tuple(forms),
        ),
        tuple(starts),
        tuple(pages),
    )


def _shift_form(form: FormValue, dy: float) -> FormValue:
    x0, y0, x1, y1 = form.box
    return replace(form, box=(x0, y0 + dy, x1, y1 + dy))


def _shift(line: OcrLine, dy: float) -> OcrLine:
    if dy == 0:
        return line
    polygon = tuple(Point(p.x, p.y + dy) for p in line.polygon)
    return OcrLine(line.text, polygon, line.confidence)  # type: ignore[arg-type]


def stack_images(images: Sequence[np.ndarray | None], stacked: Stacked) -> np.ndarray | None:
    """Stack page images into one (same positions as stack_pages) on a white background. Not stacked
    if any page lacks an image."""
    if any(image is None for image in images):
        return None
    pictures = [image for image in images if image is not None]
    if len(pictures) == 1:
        return pictures[0]
    width = max(p.shape[1] for p in pictures)
    height = round(stacked.starts[-1]) + pictures[-1].shape[0]
    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    for start, picture in zip(stacked.starts, pictures, strict=True):
        top = round(start)
        canvas[top : top + picture.shape[0], : picture.shape[1]] = picture[:, :, :3]
    return canvas


@dataclass
class StackedLayout:
    """The layout model looks at pages one by one (a downscaled tall page would be illegible) and
    its regions are moved back to their positions on the tall page."""

    base: LayoutDetector
    starts: tuple[float, ...]
    heights: tuple[int, ...]
    name: str

    def detect(self, image: np.ndarray) -> list[Region]:
        found: list[Region] = []
        for start, height in zip(self.starts, self.heights, strict=True):
            top = round(start)
            for r in self.base.detect(image[top : top + height]):
                x0, y0, x1, y1 = r.box
                found.append(Region(r.kind, (x0, y0 + top, x1, y1 + top), r.score))
        return found


def stacked_engines(engines: StructureEngines, stacked: Stacked) -> StructureEngines:
    """Models used for stacked pages: the layout model looks page by page; the table model looks at
    a single table image and needs no change."""
    if engines.layout is None or len(stacked.pages) == 1:
        return engines
    heights = tuple(p.height for p in stacked.pages)
    layout = StackedLayout(engines.layout, stacked.starts, heights, engines.layout.name)
    return StructureEngines(layout, engines.table)


def local_box(box: Sequence[float], starts: Sequence[float]) -> tuple[int, list[float]]:
    """Box on the tall page → (page index, 0-based; box on that page). Boxes spanning two pages
    belong to the upper one."""
    index = 0
    for k, start in enumerate(starts):
        if box[1] >= start - GAP / 2:
            index = k
    top = starts[index]
    x0, y0, x1, y1 = box
    return index, [x0, y0 - top, x1, y1 - top]


def ocr_page_json(ocr: dict[str, Any] | None, page: int, first: int) -> dict[str, Any] | None:
    """OCR result of page page (page number in the file) in one extraction.

        A single-page document stores that page; a multi-page document stores {"pages": [{"page":
    3, "lines": …}, …]}."""
    if ocr is None:
        return None
    if "pages" not in ocr:
        return ocr if page == first else None
    return next((p for p in ocr["pages"] if p.get("page") == page), None)


def pages_json(ocr: dict[str, Any] | None, first: int) -> list[tuple[int, dict[str, Any]]]:
    """OCR result of every page in one extraction: [(page number, OCR of that page)]."""
    if ocr is None:
        return []
    if "pages" not in ocr:
        return [(first, ocr)]
    return [(int(p["page"]), p) for p in ocr["pages"]]


def covering(extractions: Iterable[Extraction], page: int) -> Extraction | None:
    """Document (extraction) containing page page."""
    return next((e for e in extractions if e.page <= page <= (e.last_page or e.page)), None)


def page_slice(data: dict[str, Any], page: int, first: int) -> tuple[float, float] | None:
    """Range (top, bottom) of page page on the tall page of a stacked structure. None if not stacked
    (single page)."""
    starts = data.get("page_starts") or []
    if len(starts) < 2:
        return None
    index = page - first
    if not 0 <= index < len(starts):
        return (0.0, 0.0)
    top = float(starts[index])
    bottom = float(starts[index + 1]) if index + 1 < len(starts) else float("inf")
    return (top, bottom)
