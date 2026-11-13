"""局部放大再识别（ADR 0042）：字段有冲突、可能漏读时，把那一块放大重新画、重新认一次。

PDF 按 ZOOM_SCALE 重新渲染这一页（比整页 OCR 时清楚得多）；图片用原图（整页 OCR 时可能被缩小过）。
结果只当作一个候选，不会自己替换掉值。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jacara.pipeline.pages import local_box
from jacara.pipeline.region import read_region
from jacara.plugins.aligners.base import Candidate
from jacara.plugins.ocr.base import OcrEngine
from jacara.plugins.ocr.loader import IMAGE_SUFFIXES, PDF_SCALE, page_png

log = logging.getLogger(__name__)

ZOOM_SCALE = 5.0
"""局部放大时 PDF 的渲染倍数：5 × 72 = 360 DPI（整页 OCR 用的是 216 DPI）。"""


@dataclass
class Zoom:
    """一张单据（file 的第 first 页起，几页拼成一页时 starts 是每页的起点）的局部放大再识别。"""

    file: Path
    ocr: OcrEngine
    first: int = 1
    starts: Sequence[float] = (0.0,)
    _pages: dict[int, bytes] = field(default_factory=dict)

    @property
    def is_pdf(self) -> bool:
        return self.file.suffix.lower() == ".pdf"

    @property
    def dpi(self) -> int:
        return round(72 * ZOOM_SCALE) if self.is_pdf else 0

    def _png(self, index: int) -> bytes:
        if index not in self._pages:
            data = self.file.read_bytes()
            if self.is_pdf:
                self._pages[index] = page_png(data, True, self.first + index, scale=ZOOM_SCALE)
            elif self.file.suffix.lower() in IMAGE_SUFFIXES:
                self._pages[index] = page_png(data, False, 1)
            else:
                raise ValueError(self.file.suffix)
        return self._pages[index]

    def __call__(self, box: Sequence[float]) -> Candidate | None:
        """长页上 box 这一块再认一次。没认出字返回 None。"""
        index, local = local_box(box, self.starts)
        factor = ZOOM_SCALE / PDF_SCALE if self.is_pdf else 1.0
        try:
            text, confidence = read_region(self._png(index), [v * factor for v in local], self.ocr)
        except Exception:  # 放大再认只是多一个读法：出错（文件读不了、框太小）就当没认出来
            log.warning("局部放大再识别出错：%s %s", self.file.name, box, exc_info=True)
            return None
        if not text:
            return None
        where = f"局部放大到 {self.dpi} DPI 再识别" if self.is_pdf else "用原图局部再识别"
        x0, y0, x1, y1 = box
        return Candidate(
            "zoom", text, (x0, y0, x1, y1), confidence, note=where, dpi=self.dpi or None
        )
