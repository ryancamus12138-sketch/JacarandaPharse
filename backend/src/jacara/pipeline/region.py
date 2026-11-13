"""重新识别页面上的一块区域：审核时 OCR 认错、漏认的地方，框出来单独再认一次。

小块文字放大以后，OCR 常常就认对了（整页识别时一个字只有十几个像素高）。
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from PIL import Image

from jacara.plugins.ocr.base import OcrEngine
from jacara.plugins.ocr.layout import group_rows

PADDING = 0.3
"""框的四周多留一些（框高的 30%，至少 8 像素）：框画得紧时，字的边缘不会被切掉，
OCR 的"检测"也需要字周围有一点空白。"""

log = logging.getLogger(__name__)

TARGET_HEIGHT = 96
"""裁下来的图太矮时放大到至少这么高（像素）。"""


def crop_region(png: bytes, box: list[float], padding: float = PADDING) -> Image.Image:
    """页面图片上 box 这一块，四周多留一点。框太小报 ValueError。"""
    image = Image.open(io.BytesIO(png))
    x0, y0, x1, y1 = (round(v) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    pad = max(8, round((y1 - y0) * padding))
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(image.width, x1 + pad), min(image.height, y1 + pad)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError("框太小了，请框住要识别的文字")
    return image.crop((x0, y0, x1, y1)).convert("RGB")


LOOK_MIN_SIDE = 64
"""给视觉模型的小图至少这么高、这么宽：太小的图模型看不清。"""
LOOK_MAX_SIDE = 1024


def region_png(png: bytes, box: list[float]) -> bytes:
    """框里这一块（四周多留一点）→ 给视觉模型看的 PNG（ADR 0038）。太小的放大，太大的缩小。"""
    crop = crop_region(png, box)
    short = min(crop.width, crop.height)
    if short < LOOK_MIN_SIDE:
        scale = LOOK_MIN_SIDE / short
        crop = crop.resize(
            (round(crop.width * scale), round(crop.height * scale)), Image.Resampling.LANCZOS
        )
    crop.thumbnail((LOOK_MAX_SIDE, LOOK_MAX_SIDE))
    out = io.BytesIO()
    crop.save(out, format="PNG")
    return out.getvalue()


def read_region(png: bytes, box: list[float], engine: OcrEngine) -> tuple[str, float | None]:
    """页面图片（PNG）上 box=[x0, y0, x1, y1] 这一块的文字和最低置信度。没认出文字返回 ("", None)。

    多行文字按从上到下、从左到右用空格连起来。
    """
    crop = crop_region(png, box)
    if crop.height < TARGET_HEIGHT:
        scale = TARGET_HEIGHT / crop.height
        crop = crop.resize((round(crop.width * scale), TARGET_HEIGHT), Image.Resampling.LANCZOS)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "region.png"
        crop.save(path)
        page = engine.recognize(path)
        if not page.lines:
            # 检测没找到字：整块当成一行直接识别（引擎支持的话）
            read_line = getattr(engine, "read_line", None)
            found = read_line(path) if read_line else None
            log.info("重新识别 %s：检测没找到字，直接识别 → %r", box, found)
            return (" ".join(found[0].split()), found[1]) if found else ("", None)
    text = " ".join(row.text.replace("  ", " ") for row in group_rows(page))
    log.info("重新识别 %s → %r", box, text)
    return " ".join(text.split()), min(line.confidence for line in page.lines)
