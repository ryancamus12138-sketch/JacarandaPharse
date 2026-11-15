"""When the model is shown the page image, and conversion of page images to PNG for the model.

Normally the model sees only the OCR text (fast, and evidence can be checked block by block).
Photos and scans with handwriting, stamps or blur cause OCR misreads and misses; then the page
image is sent along (requires the vision projector mmproj).

- off: never;
- always: every document (slow; for documents with much handwriting);
- auto: only when OCR is unreliable: photos or scans (not the PDF text layer) with low average
  confidence, many low-confidence blocks, or almost no recognized text."""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Literal

import numpy as np
from PIL import Image

from jacara.core.i18n import stored
from jacara.plugins.ocr.base import OcrPage

VisionMode = Literal["auto", "always", "off"]

LOW_MEAN = 0.88
"""Average full-page OCR confidence below this is unreliable."""
LOW_LINE = 0.75
"""A text block with confidence below this counts as not clearly recognized."""
LOW_SHARE = 0.2
"""More than 20% of blocks not clearly recognized is unreliable."""
FEW_LINES = 5
"""Fewer recognized text blocks than this on a page (usually handwriting or a blurry photo) is
unreliable."""

MAX_SIDE = 1600
"""Maximum length in pixels of the longer side of images sent to the model. Larger images reveal
nothing more and only cost time and context."""
MAX_IMAGES = 4
"""Maximum number of page images per document. Longer documents send only the first pages."""


def weak_ocr(page: OcrPage) -> str | None:
    """Why OCR of this page is unreliable; None if reliable (or from the PDF text layer)."""
    if page.engine == "pdf-text":
        return None
    if len(page.lines) < FEW_LINES:
        return stored("仅识别出 {n} 块文字", n=len(page.lines))
    scores = [line.confidence for line in page.lines]
    mean = sum(scores) / len(scores)
    low = sum(1 for c in scores if c < LOW_LINE) / len(scores)
    if mean < LOW_MEAN:
        return stored("OCR 平均置信度仅为 {mean:.2f}", mean=mean)
    if low > LOW_SHARE:
        return stored("{low:.0%} 的文字块 OCR 识别置信度低", low=low)
    return None


def vision_reason(pages: Sequence[OcrPage], mode: VisionMode) -> str | None:
    """Whether this document should be shown to the model as an image, and why."""
    if mode == "off":
        return None
    if mode == "always":
        return stored("已设置为每张都读取原图")
    for number, page in enumerate(pages, start=1):
        weak = weak_ocr(page)
        if weak is not None:
            where = stored("第 {number} 页", number=number) if len(pages) > 1 else stored("此单据")
            return stored("{where}是照片或扫描件，{weak}", where=where, weak=weak)
    return None


def to_png(image: np.ndarray, max_side: int = MAX_SIDE) -> bytes:
    """OpenCV BGR array → PNG with the longer side scaled down to at most max_side."""
    picture = Image.fromarray(np.ascontiguousarray(image[:, :, ::-1]))
    picture.thumbnail((max_side, max_side))
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue()
