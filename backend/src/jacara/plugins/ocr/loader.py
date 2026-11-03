"""把任何输入文件（图片、PDF）变成 OcrPage 列表，一页一个。

- 图片：直接交给 OCR 引擎。
- 有文字层的 PDF（电脑生成的发票）：用 pdfplumber 直接读出文字和坐标，不用 OCR，又快又准。
- 没有文字层的 PDF（扫描件、拍照转的 PDF）：用 pypdfium2 把每页渲染成图片，再交给 OCR。

后面的抽取、对齐、校验只认 OcrPage，不用关心原文件是什么格式。
"""

from __future__ import annotations

import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from pdfplumber.page import Page

from jacara.plugins.ocr.base import OcrEngine, OcrLine, OcrPage, Point

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

PDF_SCALE = 3
"""PDF 坐标单位是"点"（1/72 英寸）。乘以 3 相当于 216 DPI：扫描件 OCR 够清楚，
文字层的坐标也乘同样的倍数，两种 PDF 的坐标单位一致（都是渲染后图片上的像素）。"""

MIN_TEXT_CHARS = 10
"""一页 PDF 的文字层少于这么多字，就当作扫描件，改用 OCR。"""


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
            if text_page is not None:
                pages.append(text_page)
            else:
                pages.append(_render_and_ocr(path, number, ocr))
    return pages


def _text_layer(page: Page) -> OcrPage | None:
    """读 PDF 自带的文字和坐标。文字太少（多半是扫描件）返回 None。"""
    start = time.perf_counter()
    # keep_blank_chars：同一行里挨得近的词合成一段（"Invoice Number:"），离得远的分开。
    words = page.extract_words(keep_blank_chars=True, x_tolerance=3)
    if sum(len(w["text"].strip()) for w in words) < MIN_TEXT_CHARS:
        return None

    lines: list[OcrLine] = []
    for w in words:
        text = w["text"].strip()
        if not text:
            continue
        x0, x1 = w["x0"] * PDF_SCALE, w["x1"] * PDF_SCALE
        y0, y1 = w["top"] * PDF_SCALE, w["bottom"] * PDF_SCALE
        polygon = (Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1))
        lines.append(OcrLine(text=text, polygon=polygon, confidence=1.0))

    return OcrPage(
        width=round(page.width * PDF_SCALE),
        height=round(page.height * PDF_SCALE),
        lines=tuple(lines),
        engine="pdf-text",
        models={"engine": f"pdfplumber {version('pdfplumber')}", "source": "PDF 文字层"},
        duration_ms=round((time.perf_counter() - start) * 1000),
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
