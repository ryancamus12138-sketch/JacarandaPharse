"""一个文件里的几张单据（ADR 0036）：把一张单据的几页拼成一页处理，存的时候再拆回每一页。

为什么拼成一页：抽取、找出处、版面分析都是按"一页"写的。两页的酒店账单拼成上下相连的一张长页，
跨页的明细表就自然接在一起，后面的步骤一行都不用改。
存的时候每一页的 OCR 结果、出处框都换回这一页自己的坐标，审核页照常按页显示原图。
"""

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
"""拼起来时页与页之间空多少像素：比行距大得多，分析时不会把上一页最后一行和下一页第一行当成一行。"""


@dataclass(frozen=True)
class Stacked:
    """几页拼成的一页。starts[k] 是第 k 页（从 0 数）在长页上从哪个高度开始。"""

    page: OcrPage
    starts: tuple[float, ...]
    pages: tuple[OcrPage, ...]

    def page_index(self, y: float) -> int:
        """长页上高度 y 落在第几页（从 0 数）。"""
        index = 0
        for k, start in enumerate(self.starts):
            if y >= start - GAP / 2:
                index = k
        return index


def stack_pages(pages: Sequence[OcrPage]) -> Stacked:
    """几页拼成一页：上下相连，中间空 GAP。只有一页就原样返回。"""
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
    """几页的图拼成一张（和 stack_pages 一样的位置），白底。有一页没有图就不拼。"""
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
    """版面模型按一页一页看（拼成的长页缩小后看不清），找到的区域再挪回长页上的位置。"""

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
    """拼成长页时用的模型：版面模型一页一页看；表格模型看的是一张表的图，不用改。"""
    if engines.layout is None or len(stacked.pages) == 1:
        return engines
    heights = tuple(p.height for p in stacked.pages)
    layout = StackedLayout(engines.layout, stacked.starts, heights, engines.layout.name)
    return StructureEngines(layout, engines.table)


def local_box(box: Sequence[float], starts: Sequence[float]) -> tuple[int, list[float]]:
    """长页上的框 → （第几页，从 0 数；这一页上的框）。框跨两页时算在上面那页。"""
    index = 0
    for k, start in enumerate(starts):
        if box[1] >= start - GAP / 2:
            index = k
    top = starts[index]
    x0, y0, x1, y1 = box
    return index, [x0, y0 - top, x1, y1 - top]


def ocr_page_json(ocr: dict[str, Any] | None, page: int, first: int) -> dict[str, Any] | None:
    """一条抽取记录里第 page 页（文件里的页码）的 OCR 结果。

    一张单据只占一页的，存的就是这一页；占好几页的，存成 {"pages": [{"page": 3, "lines": …}, …]}。
    """
    if ocr is None:
        return None
    if "pages" not in ocr:
        return ocr if page == first else None
    return next((p for p in ocr["pages"] if p.get("page") == page), None)


def pages_json(ocr: dict[str, Any] | None, first: int) -> list[tuple[int, dict[str, Any]]]:
    """一条抽取记录里每一页的 OCR 结果：[(页码, 那一页的 OCR)]。"""
    if ocr is None:
        return []
    if "pages" not in ocr:
        return [(first, ocr)]
    return [(int(p["page"]), p) for p in ocr["pages"]]


def covering(extractions: Iterable[Extraction], page: int) -> Extraction | None:
    """第 page 页属于哪一张单据（哪一条抽取记录）。"""
    return next((e for e in extractions if e.page <= page <= (e.last_page or e.page)), None)


def page_slice(data: dict[str, Any], page: int, first: int) -> tuple[float, float] | None:
    """几页拼成的结构里，第 page 页占长页的哪一段 (上, 下)。不是拼成的（只有一页）返回 None。"""
    starts = data.get("page_starts") or []
    if len(starts) < 2:
        return None
    index = page - first
    if not 0 <= index < len(starts):
        return (0.0, 0.0)
    top = float(starts[index])
    bottom = float(starts[index + 1]) if index + 1 < len(starts) else float("inf")
    return (top, bottom)
