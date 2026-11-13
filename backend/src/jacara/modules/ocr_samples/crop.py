"""裁图和分类（ADR 0050），不碰数据库。

裁图要和首轮识别时识别模型看到的图一样，"OCR 读成什么"和"那张图"才严格对应：
RapidOCR 先找出每一行字的四个角，再把这个四边形透视拉正成一张小图交给识别模型
（竖着的转 90°）。这里用同样的做法。
"""

from __future__ import annotations

import difflib
import hashlib
import io
import unicodedata
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image

CATEGORIES: dict[str, str] = {
    "rec_error": "认错字",
    "correct": "认对的行",
    "missed": "漏认（人工补框）",
    "unclear": "原因未确定",
}
"""分类 → 界面上的名字。"""

TAGS: dict[str, str] = {
    "substitution": "字认错",
    "missing": "漏字",
    "extra": "多字",
    "format": "空格、大小写、全角半角",
    "empty": "整行没认出",
}
"""错误标签 → 界面上的名字。"""

BAD_CHARS = ("\t", "\r", "\n")
"""标签里不能有的字：rec_gt.txt 一行一条、路径和文字之间用 Tab 隔开。"""


def crop_polygon(png: bytes, polygon: Sequence[Sequence[float]]) -> Image.Image:
    """按四个角（左上、右上、右下、左下）透视裁下一行字，拉正。和 RapidOCR 给识别模型的图一样。"""
    import cv2

    image = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
    points = np.array([[float(x), float(y)] for x, y in polygon], dtype=np.float32)
    if points.shape != (4, 2):
        raise ValueError("要四个角")
    width = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    height = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    if width < 2 or height < 2:
        raise ValueError("框太小了")
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(points, target)
    crop = cv2.warpPerspective(
        image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC
    )
    if crop.shape[0] / crop.shape[1] >= 1.5:  # 竖着的一行：转过来
        crop = np.rot90(crop)
    return Image.fromarray(np.ascontiguousarray(crop))


def box_polygon(box: Sequence[float]) -> list[list[float]]:
    """框 (x0, y0, x1, y1) → 四个角。反着拖的框也行。"""
    x0, y0, x1, y1 = box
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean(text: str) -> str | None:
    """整理人填的图中原文：去掉首尾空白；有 Tab、换行（存不进 rec_gt.txt）或是空的返回 None。"""
    text = text.strip()
    if not text or any(c in text for c in BAD_CHARS):
        return None
    return text


def _loose(text: str) -> str:
    """去掉空白，不分大小写、全角半角：看两串字是不是只差这些。标点要算（1.00 和 100 不一样）。"""
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(text.split())


def error_tags(ocr_text: str | None, truth: str) -> list[str]:
    """OCR 读的 → 图中原文，差在哪几种地方。一样的话是空的。"""
    if ocr_text is None or ocr_text == truth:
        return []
    if not ocr_text.strip():
        return ["empty"]
    if _loose(ocr_text) == _loose(truth):
        return ["format"]
    tags: list[str] = []
    for op, *_ in difflib.SequenceMatcher(None, ocr_text, truth, autojunk=False).get_opcodes():
        tag = {"replace": "substitution", "insert": "missing", "delete": "extra"}.get(op)
        if tag and tag not in tags:
            tags.append(tag)
    return [t for t in TAGS if t in tags]  # 固定顺序


def category_of(kind: str, ocr_text: str | None, truth: str) -> str:
    """自动分类：点一块原文的，改了是认错字、没改是认对的；框选的，框里没文字块是漏认，有是说不清。"""
    if kind == "line":
        return "correct" if ocr_text == truth else "rec_error"
    return "missed" if ocr_text is None else "unclear"


def lines_in_box(lines: Sequence[dict[str, Any]], box: Sequence[float]) -> list[dict[str, Any]]:
    """框里的 OCR 文字块：一半以上落在框里的（和审核页框选时一样）。"""
    x0, y0, x1, y1 = box_polygon(box)[0] + box_polygon(box)[2]
    found = []
    for line in lines:
        a0, b0, a1, b1 = (float(v) for v in line["box"])
        w = min(a1, x1) - max(a0, x0)
        h = min(b1, y1) - max(b0, y0)
        area = (a1 - a0) * (b1 - b0)
        if w > 0 and h > 0 and area > 0 and w * h / area >= 0.5:
            found.append(line)
    return found
