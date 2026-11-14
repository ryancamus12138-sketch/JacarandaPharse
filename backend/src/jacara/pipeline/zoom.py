"""Zoomed re-recognition: for conflicting or possibly missed fields, re-render the region enlarged
and recognize it again.

PDF pages are re-rendered at ZOOM_SCALE (much sharper than for full-page OCR); images use the
original (full-page OCR may have downscaled it). The result is only a candidate and never
replaces the value automatically."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from jacara.core.i18n import stored
from jacara.pipeline.pages import local_box
from jacara.pipeline.region import read_region
from jacara.plugins.aligners.base import Candidate
from jacara.plugins.ocr.base import OcrEngine
from jacara.plugins.ocr.loader import IMAGE_SUFFIXES, PDF_SCALE, page_png

log = logging.getLogger(__name__)

ZOOM_SCALE = 5.0
"""PDF render scale for zoomed regions: 5 × 72 = 360 DPI (full-page OCR uses 216 DPI)."""


@dataclass
class Zoom:
    """Zoomed re-recognition for one document (starting at page first of file; starts holds the page
    offsets when pages are stacked)."""

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
        """Recognize box on the tall page again. None if no text is recognized."""
        index, local = local_box(box, self.starts)
        factor = ZOOM_SCALE / PDF_SCALE if self.is_pdf else 1.0
        try:
            text, confidence = read_region(self._png(index), [v * factor for v in local], self.ocr)
        # Zoomed re-recognition only adds a reading: on errors (unreadable file, box too small)
        # treat it as nothing recognized.
        except Exception:
            log.warning("局部放大再识别出错：%s %s", self.file.name, box, exc_info=True)
            return None
        if not text:
            return None
        where = (
            stored("局部放大至 {dpi} DPI 后识别", dpi=self.dpi)
            if self.is_pdf
            else stored("使用原图局部重新识别")
        )
        x0, y0, x1, y1 = box
        return Candidate(
            "zoom", text, (x0, y0, x1, y1), confidence, note=where, dpi=self.dpi or None
        )
