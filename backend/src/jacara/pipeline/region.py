"""Re-recognize a region of a page: text that OCR misread or missed during review is selected and
recognized again on its own.

Once enlarged, small text is often recognized correctly (in full-page OCR a character may be only
a dozen pixels tall)."""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from PIL import Image

from jacara.core.i18n import stored
from jacara.plugins.ocr.base import OcrEngine
from jacara.plugins.ocr.layout import group_rows

PADDING = 0.3
"""Padding around the box (30% of the box height, at least 8 pixels): character edges are not cut
off by tight boxes, and OCR detection needs some white space around text."""

log = logging.getLogger(__name__)

TARGET_HEIGHT = 96
"""Crops shorter than this are upscaled to at least this height (pixels)."""


def crop_region(png: bytes, box: list[float], padding: float = PADDING) -> Image.Image:
    """Region box of the page image with padding. Raises ValueError for boxes that are too small."""
    image = Image.open(io.BytesIO(png))
    x0, y0, x1, y1 = (round(v) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    pad = max(8, round((y1 - y0) * padding))
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(image.width, x1 + pad), min(image.height, y1 + pad)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError(stored("框太小了，请框住要识别的文字"))
    return image.crop((x0, y0, x1, y1)).convert("RGB")


LOOK_MIN_SIDE = 64
"""Minimum height and width of crops sent to the vision model: the model cannot read images that are
too small."""
LOOK_MAX_SIDE = 1024


def region_png(png: bytes, box: list[float]) -> bytes:
    """Region inside the box (with padding) → PNG for the vision model. Small regions are upscaled
    and large ones downscaled."""
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
    """Text and lowest confidence of box=[x0, y0, x1, y1] on the page image (PNG). Returns ("",
    None) if no text is recognized.

        Multiple lines are joined with spaces, top to bottom and left to right."""
    crop = crop_region(png, box)
    if crop.height < TARGET_HEIGHT:
        scale = TARGET_HEIGHT / crop.height
        crop = crop.resize((round(crop.width * scale), TARGET_HEIGHT), Image.Resampling.LANCZOS)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "region.png"
        crop.save(path)
        page = engine.recognize(path)
        if not page.lines:
            # Detection found no text: recognize the whole region as one line (if the engine
            # supports it).
            read_line = getattr(engine, "read_line", None)
            found = read_line(path) if read_line else None
            log.info("重新识别 %s：检测没找到字，直接识别 → %r", box, found)
            return (" ".join(found[0].split()), found[1]) if found else ("", None)
    text = " ".join(row.text.replace("  ", " ") for row in group_rows(page))
    log.info("重新识别 %s → %r", box, text)
    return " ".join(text.split()), min(line.confidence for line in page.lines)
