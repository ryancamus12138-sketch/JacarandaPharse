"""Cropping and classification; no database access.

The crop must equal the image the recognition model saw during the first OCR pass, so that "what
OCR read" corresponds exactly to "that image": RapidOCR first finds the four corners of each text
line, then perspective-warps that quadrilateral into a small upright image for the recognition
model (rotating vertical lines by 90°). The same procedure is used here."""

from __future__ import annotations

import difflib
import hashlib
import io
import unicodedata
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image

from jacara.core.i18n import N_, stored

CATEGORIES: dict[str, str] = {
    "rec_error": N_("识别错误"),
    "correct": N_("识别正确的行"),
    "missed": N_("漏识别（人工补框）"),
    "unclear": N_("原因未确定"),
}
"""Category → display name."""

TAGS: dict[str, str] = {
    "substitution": N_("字符识别错误"),
    "missing": N_("缺字"),
    "extra": N_("多字"),
    "format": N_("空格、大小写、全角/半角"),
    "empty": N_("整行未识别"),
}
"""Error label → display name."""

BAD_CHARS = ("\t", "\r", "\n")
"""Characters not allowed in labels: rec_gt.txt has one entry per line with a Tab between path and
text."""


def crop_polygon(png: bytes, polygon: Sequence[Sequence[float]]) -> Image.Image:
    """Perspective-crop and rectify one text line from its four corners (top left, top right, bottom
    right, bottom left), as RapidOCR prepares input for the recognition model."""
    import cv2

    image = np.array(Image.open(io.BytesIO(png)).convert("RGB"))
    points = np.array([[float(x), float(y)] for x, y in polygon], dtype=np.float32)
    if points.shape != (4, 2):
        raise ValueError(stored("需要四个角点"))
    width = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    height = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    if width < 2 or height < 2:
        raise ValueError(stored("框过小"))
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(points, target)
    crop = cv2.warpPerspective(
        image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC
    )
    if crop.shape[0] / crop.shape[1] >= 1.5:  # Vertical line: rotate it.
        crop = np.rot90(crop)
    return Image.fromarray(np.ascontiguousarray(crop))


def box_polygon(box: Sequence[float]) -> list[list[float]]:
    """Box (x0, y0, x1, y1) → four corners. Boxes dragged in reverse are accepted."""
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
    """Normalize the transcription entered by the reviewer: strip surrounding whitespace; None if
    empty or containing a Tab or newline (not storable in rec_gt.txt)."""
    text = text.strip()
    if not text or any(c in text for c in BAD_CHARS):
        return None
    return text


def _loose(text: str) -> str:
    """Remove whitespace and ignore case and full-width/half-width forms, to check whether two
    strings differ only in these. Punctuation counts (1.00 differs from 100)."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(text.split())


def error_tags(ocr_text: str | None, truth: str) -> list[str]:
    """Kinds of differences between the OCR reading and the transcription. Empty when identical."""
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
    return [t for t in TAGS if t in tags]  # Fixed order.


def category_of(kind: str, ocr_text: str | None, truth: str) -> str:
    """Automatic category: for a clicked source block, rec_error if changed and correct if
    unchanged; for a selected region, missed if it contains no text block, unclear otherwise."""
    if kind == "line":
        return "correct" if ocr_text == truth else "rec_error"
    return "missed" if ocr_text is None else "unclear"


def lines_in_box(lines: Sequence[dict[str, Any]], box: Sequence[float]) -> list[dict[str, Any]]:
    """OCR text blocks inside the box: those with more than half their area inside (as in region
    selection on the review page)."""
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
