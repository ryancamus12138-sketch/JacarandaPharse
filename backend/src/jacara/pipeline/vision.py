"""什么时候让模型看原图（ADR 0038），以及把页面图变成给模型的 PNG。

模型平时只看 OCR 认出来的文字（又快又能逐块核对出处）。照片、扫描件上有手写、印章、
模糊的地方，OCR 会认错或漏掉，这时把原图一起给模型（要加载视觉模型 mmproj）。

- off：不看图；
- always：每张单据都看（慢，适合手写多的单据）；
- auto：只在 OCR 不可靠时看：照片或扫描件（不是 PDF 文字层），而且平均置信度低、
  低置信度的文字块多，或者几乎没认出字。
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Literal

import numpy as np
from PIL import Image

from jacara.plugins.ocr.base import OcrPage

VisionMode = Literal["auto", "always", "off"]

LOW_MEAN = 0.88
"""整页 OCR 平均置信度低于它，算不可靠。"""
LOW_LINE = 0.75
"""一块文字的置信度低于它，算"没认清"。"""
LOW_SHARE = 0.2
"""没认清的块超过两成，算不可靠。"""
FEW_LINES = 5
"""整页认出的文字块少于它（多半是手写或照片太糊），算不可靠。"""

MAX_SIDE = 1600
"""给模型的图最长边多少像素。再大模型也看不出更多，只是更慢、更占上下文。"""
MAX_IMAGES = 4
"""一张单据最多给几页图。更多页的单据只给前几页。"""


def weak_ocr(page: OcrPage) -> str | None:
    """这一页的 OCR 不可靠的原因；可靠（或者是 PDF 文字层）返回 None。"""
    if page.engine == "pdf-text":
        return None
    if len(page.lines) < FEW_LINES:
        return f"只认出 {len(page.lines)} 块文字"
    scores = [line.confidence for line in page.lines]
    mean = sum(scores) / len(scores)
    low = sum(1 for c in scores if c < LOW_LINE) / len(scores)
    if mean < LOW_MEAN:
        return f"OCR 平均置信度只有 {mean:.2f}"
    if low > LOW_SHARE:
        return f"{low:.0%} 的文字块 OCR 没认清"
    return None


def vision_reason(pages: Sequence[OcrPage], mode: VisionMode) -> str | None:
    """这张单据要不要看原图，要的话为什么。"""
    if mode == "off":
        return None
    if mode == "always":
        return "设置成每张都看原图"
    for number, page in enumerate(pages, start=1):
        weak = weak_ocr(page)
        if weak is not None:
            where = f"第 {number} 页" if len(pages) > 1 else "这张单据"
            return f"{where}是照片或扫描件，{weak}"
    return None


def to_png(image: np.ndarray, max_side: int = MAX_SIDE) -> bytes:
    """OpenCV 的 BGR 数组 → 缩到最长边不超过 max_side 的 PNG。"""
    picture = Image.fromarray(np.ascontiguousarray(image[:, :, ::-1]))
    picture.thumbnail((max_side, max_side))
    out = io.BytesIO()
    picture.save(out, format="PNG", optimize=True)
    return out.getvalue()
