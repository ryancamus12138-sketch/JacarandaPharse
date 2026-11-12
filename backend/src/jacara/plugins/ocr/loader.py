"""把任何输入文件（图片、PDF）变成 OcrPage 列表，一页一个。

- 图片：直接交给 OCR 引擎。
- 有文字层的 PDF（电脑生成的发票）：用 pdfplumber 直接读出文字和坐标，不用 OCR，又快又准。
- 没有文字层的 PDF（扫描件、拍照转的 PDF）：用 pypdfium2 把每页渲染成图片，再交给 OCR。

后面的抽取、对齐、校验只认 OcrPage，不用关心原文件是什么格式。
"""

from __future__ import annotations

import io
import re
import tempfile
import time
import unicodedata
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from pdfplumber.page import Page
from PIL import Image, ImageOps

from jacara.plugins.ocr.base import OcrEngine, OcrLine, OcrPage, Point

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

PDF_SCALE = 3
"""PDF 坐标单位是"点"（1/72 英寸）。乘以 3 相当于 216 DPI：扫描件 OCR 够清楚，
文字层的坐标也乘同样的倍数，两种 PDF 的坐标单位一致（都是渲染后图片上的像素）。"""

MIN_TEXT_CHARS = 10
"""一页 PDF 的文字层少于这么多字，就当作扫描件，改用 OCR。"""

MAX_GARBLED = 0.15
"""文字层里认不出来的字超过这个比例，就当作乱码，改用 OCR。

有的 PDF 字体没有带"字形 → 文字"的对照表，读出来是 (cid:12)、方框、私用区字符；
有的编码错了，读出来是 Ã©、â€ 这种。照着乱码抽字段，值和出处都是错的。"""

_MOJIBAKE = re.compile("[ÃÂâ][\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]")
"""UTF-8 被当成 Latin-1 读出来的样子：é → Ã©，" → â€œ。"""


def garbled_ratio(text: str) -> float:
    """文字里认不出来的字占多少（0～1）。

    算作认不出来的：(cid:数字)、替换符 �、方框 □、私用区和未分配的字符、控制字符、
    UTF-8 当 Latin-1 读出来的乱码（Ã©）。空格不算。
    """
    cids = re.findall(r"\(cid:\d+\)", text)
    rest = re.sub(r"\(cid:\d+\)", "", text)
    chars = [c for c in rest if not c.isspace()]
    total = len(chars) + len(cids)
    if total == 0:
        return 0.0
    bad = len(cids) + 2 * len(_MOJIBAKE.findall(rest))
    for c in chars:
        category = unicodedata.category(c)
        if c in "\ufffd□" or category in ("Co", "Cn", "Cc", "Cs"):
            bad += 1
    return min(1.0, bad / total)


class UnsupportedFileError(ValueError):
    """不支持的文件类型。"""


def load_pages(path: Path, ocr: OcrEngine) -> list[OcrPage]:
    """读一个文件，返回每一页的 OcrPage。"""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return [ocr.recognize(path)]
    if suffix == ".pdf":
        return _load_pdf(path, ocr)
    raise UnsupportedFileError(f"不支持的文件类型：{suffix}，支持图片和 PDF")


def _load_pdf(path: Path, ocr: OcrEngine) -> list[OcrPage]:
    pages: list[OcrPage] = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages):
            text_page = _text_layer(page)
            if text_page is None:
                pages.append(_render_and_ocr(path, number, ocr))
                continue
            ratio = garbled_ratio(" ".join(line.text for line in text_page.lines))
            if ratio > MAX_GARBLED:
                # 文字层是乱码：照着它抽，值和出处都是错的，改看图识别
                scanned = _render_and_ocr(path, number, ocr)
                note = f"PDF 文字层是乱码（{ratio:.0%} 的字认不出来），这一页改用 OCR"
                pages.append(replace(scanned, models={**scanned.models, "note": note}))
            else:
                pages.append(text_page)
    return pages


def _text_layer(page: Page) -> OcrPage | None:
    """读 PDF 自带的文字和坐标。文字太少（多半是扫描件）返回 None。"""
    start = time.perf_counter()
    words = page.extract_words(keep_blank_chars=False, x_tolerance=3)
    if sum(len(w["text"].strip()) for w in words) < MIN_TEXT_CHARS:
        return None

    lines: list[OcrLine] = []
    for text, x0, top, x1, bottom in _phrases(words):
        polygon = (
            Point(x0 * PDF_SCALE, top * PDF_SCALE),
            Point(x1 * PDF_SCALE, top * PDF_SCALE),
            Point(x1 * PDF_SCALE, bottom * PDF_SCALE),
            Point(x0 * PDF_SCALE, bottom * PDF_SCALE),
        )
        lines.append(OcrLine(text=text, polygon=polygon, confidence=1.0))

    return OcrPage(
        width=round(page.width * PDF_SCALE),
        height=round(page.height * PDF_SCALE),
        lines=tuple(lines),
        engine="pdf-text",
        models={"engine": f"pdfplumber {version('pdfplumber')}", "source": "PDF 文字层"},
        duration_ms=round((time.perf_counter() - start) * 1000),
    )


PHRASE_GAP = 0.6
"""同一行里两个词的空隙不超过"字高 × 0.6"（大约一两个空格），算同一段（"Invoice Number:"）；
再宽就是另一列（表格里的"入住日期"和"房间数"），分开成两块，出处框才不会横跨整行。"""


def _phrases(words: list[dict[str, Any]]) -> list[tuple[str, float, float, float, float]]:
    """PDF 里的词 → 一段一段的文字：同一行、挨得近的词连起来，离得远的分开。

    以前用 pdfplumber 的 keep_blank_chars 合并，空格多的 PDF（排版用空格对齐）会把一整行连成一块，
    出处框就从店名一直框到房间数。这里按词之间的实际空隙来分；冒号结尾的词（"Guest Name:"）
    后面也分开，后面是值。
    返回 (文字, x0, top, x1, bottom)，单位是 PDF 的点。
    """
    rows: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: (round(float(w["top"])), float(w["x0"]))):
        if not str(w["text"]).strip():
            continue
        height = float(w["bottom"]) - float(w["top"])
        for row in rows:
            last = row[-1]
            if abs(float(last["top"]) - float(w["top"])) <= max(1.0, 0.4 * height):
                row.append(w)
                break
        else:
            rows.append([w])

    phrases: list[tuple[str, float, float, float, float]] = []
    for row in rows:
        row.sort(key=lambda w: float(w["x0"]))
        current: list[dict[str, Any]] = []
        for w in row:
            if current:
                prev = current[-1]
                height = max(float(prev["bottom"]) - float(prev["top"]), 1.0)
                wide = float(w["x0"]) - float(prev["x1"]) > PHRASE_GAP * height
                # "Guest Name: Sanjay"：冒号后面是值，和标签分开，出处框只框值
                label = str(prev["text"]).rstrip().endswith((":", "："))
                if wide or label:
                    phrases.append(_phrase(current))
                    current = []
            current.append(w)
        if current:
            phrases.append(_phrase(current))
    return phrases


def _phrase(words: list[dict[str, Any]]) -> tuple[str, float, float, float, float]:
    text = " ".join(str(w["text"]).strip() for w in words)
    return (
        text,
        min(float(w["x0"]) for w in words),
        min(float(w["top"]) for w in words),
        max(float(w["x1"]) for w in words),
        max(float(w["bottom"]) for w in words),
    )


def _render_and_ocr(path: Path, number: int, ocr: OcrEngine) -> OcrPage:
    """把 PDF 的一页渲染成图片，再做 OCR。"""
    pdf = pdfium.PdfDocument(path)
    try:
        image = pdf[number].render(scale=PDF_SCALE).to_pil()
    finally:
        pdf.close()
    with tempfile.TemporaryDirectory() as folder:
        image_path = Path(folder) / f"page-{number + 1}.png"
        image.save(image_path)
        return ocr.recognize(image_path)


def page_images(path: Path) -> list[np.ndarray]:
    """文件的每一页画成图（OpenCV 用的 BGR 数组），给版面模型、表格模型看（ADR 0034）。

    坐标和 OCR 一样：PDF 按 PDF_SCALE 渲染（有文字层的页也画，文字层坐标也乘了同样倍数）；
    图片按 EXIF 方向转正。
    """
    suffix = path.suffix.lower()
    pictures: list[Image.Image] = []
    if suffix == ".pdf":
        pdf = pdfium.PdfDocument(path)
        try:
            pictures = [page.render(scale=PDF_SCALE).to_pil() for page in pdf]
        finally:
            pdf.close()
    elif suffix in IMAGE_SUFFIXES:
        with Image.open(path) as picture:
            pictures = [ImageOps.exif_transpose(picture).copy()]
    return [np.ascontiguousarray(np.asarray(p.convert("RGB"))[:, :, ::-1]) for p in pictures]


def page_png(data: bytes, is_pdf: bool, number: int) -> bytes:
    """把文件的第 number 页（从 1 开始）画成 PNG，给审核页面显示。

    坐标和 OCR 时完全一样，出处框才能画准：
    - PDF 用同样的 PDF_SCALE 渲染（文字层的坐标也乘了它）；
    - 图片先按 EXIF 方向转正：手机照片的像素常常是横着存的，另记一个"要转 90 度"的标记，
      RapidOCR 读图时会先转正再识别，坐标是转正之后的。顺便统一成 PNG：浏览器显示不了 TIFF。
    页码超出范围时抛 IndexError，由调用方先检查。
    """
    if is_pdf:
        pdf = pdfium.PdfDocument(data)
        try:
            if not 1 <= number <= len(pdf):
                raise IndexError(number)
            image = pdf[number - 1].render(scale=PDF_SCALE).to_pil()
        finally:
            pdf.close()
    else:
        if number != 1:
            raise IndexError(number)
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if image.mode not in ("RGB", "RGBA", "L"):
            image = image.convert("RGB")  # CMYK、调色板等模式先转成 RGB
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
