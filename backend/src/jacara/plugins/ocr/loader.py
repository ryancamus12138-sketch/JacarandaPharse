"""Turn any input file (image, PDF) into a list of OcrPage, one per page.

- Images: passed to the OCR engine directly.
- PDFs, default (pdf_read="image"): each page is rendered with pypdfium2 (including form
  fields) and goes through the same OCR path as photos; the embedded text layer and form field
  values are read separately and used only for cross-checking. Previously the text layer was
  used directly whenever present; PDFs that encode "示" as the radical "⽰" or whose font mapping
  is off by one glyph produced wrong values, making the PDF of a document worse than a photo of
  it.
- PDFs, pdf_read="text": read only the text layer (fast; suitable only for known-clean digital
  PDFs); pages with too little or garbled text are still rendered for OCR.

Extraction, alignment and validation depend only on OcrPage, regardless of the original file
format."""

from __future__ import annotations

import io
import re
import tempfile
import threading
import time
import unicodedata
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from pdfplumber.page import Page
from PIL import Image, ImageOps

from jacara.core.cjk import plain_cjk
from jacara.core.i18n import stored
from jacara.plugins.ocr.base import OcrEngine, OcrLine, OcrPage, Point
from jacara.plugins.ocr.pdftext import as_lines, form_values, text_words

PdfRead = Literal["image", "text"]
"""How PDF text is read: image renders pages for OCR and uses the text layer only for
cross-checking; text reads only the text layer."""

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

PDF_SCALE = 3
"""PDF coordinates are in points (1/72 inch). Scaling by 3 gives 216 DPI, sharp enough for OCR of
scans; text-layer coordinates are scaled the same way, so both kinds of PDF share one unit
(pixels of the rendered image)."""

MIN_TEXT_CHARS = 10
"""A PDF page with fewer characters than this in its text layer is treated as a scan and OCR'd."""

MAX_GARBLED = 0.15
"""A text layer with a share of unrecognizable characters above this is treated as garbled and
OCR'd.

Some PDF fonts lack a glyph-to-text mapping and yield (cid:12), boxes or private-use characters;
others have a wrong encoding and yield sequences like Ã© or â€. Extracting from garbled text
produces wrong values and evidence."""

_MOJIBAKE = re.compile("[ÃÂâ][\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]")
"""UTF-8 decoded as Latin-1: é → Ã©, " → â€œ."""


def garbled_ratio(text: str) -> float:
    """Share (0–1) of unrecognizable characters in the text.

        Counted as unrecognizable: (cid:N), the replacement character �, the box □, private-use
    and unassigned characters, control characters, and UTF-8 decoded as Latin-1 (Ã©). Spaces are
    not counted."""
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
    """Unsupported file type."""


def load_pages(path: Path, ocr: OcrEngine, pdf_read: PdfRead = "image") -> list[OcrPage]:
    """Read a file and return one OcrPage per page."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return [ocr.recognize(path)]
    if suffix == ".pdf":
        return _load_pdf(path, ocr) if pdf_read == "text" else _image_pages(path, ocr)
    raise UnsupportedFileError(stored("不支持的文件类型：{suffix}，支持图片和 PDF", suffix=suffix))


PDFIUM_LOCK = threading.RLock()
"""pdfium is not thread-safe: two threads opening or rendering PDFs concurrently fail with "Failed
to load page." The web service runs handlers in a thread pool and the review page requests
several crops at once, so every pdfium call must hold this lock. (Background workers run in
separate processes and are unaffected; holding the lock is harmless.)"""


def open_pdf(source: Path | bytes) -> pdfium.PdfDocument:
    """Open a PDF and initialize forms so that rendering includes filled form values. The caller
    must hold PDFIUM_LOCK."""
    pdf = pdfium.PdfDocument(source)
    pdf.init_forms()
    return pdf


def _image_pages(path: Path, ocr: OcrEngine) -> list[OcrPage]:
    """Render every page for OCR; the text layer and form fields are read separately and attached to
    OcrPage for cross-checking only."""
    pages: list[OcrPage] = []
    with PDFIUM_LOCK:
        pdf = open_pdf(path)
        count = len(pdf)
    try:
        for number in range(count):
            with PDFIUM_LOCK:  # Hold the lock only while using pdfium; release it during OCR.
                page = pdf[number]
                bitmap = page.render(scale=PDF_SCALE, may_draw_forms=True)
                conv = bitmap.get_posconv(page)
                words, hidden = text_words(page, conv)
                layer = as_lines(_phrases(words))
                forms = tuple(form_values(pdf, page, conv))
                picture = bitmap.to_pil()
            with tempfile.TemporaryDirectory() as folder:
                image_path = Path(folder) / f"page-{number + 1}.png"
                picture.save(image_path)
                scanned = ocr.recognize(image_path)
            models = {
                **scanned.models,
                "source": stored("PDF 每页画成图后 OCR，文字层只做核对"),
                "text_layer": f"pdfium {pdfium.PDFIUM_INFO.version}" if layer else stored("没有"),
            }
            if hidden:
                models["note"] = stored(
                    "这一页的文字层里有 {hidden} 个看不见的字（多半是别的软件留下的旧 OCR），"
                    "没有用",
                    hidden=hidden,
                )
            pages.append(replace(scanned, models=models, text_layer=layer, forms=forms))
    finally:
        with PDFIUM_LOCK:
            pdf.close()
    return pages


def _load_pdf(path: Path, ocr: OcrEngine) -> list[OcrPage]:
    """Read only the text layer (pdf_read="text"): only pages with too little or garbled text are
    rendered for OCR."""
    pages: list[OcrPage] = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages):
            text_page = _text_layer(page)
            if text_page is None:
                pages.append(_render_and_ocr(path, number, ocr))
                continue
            ratio = garbled_ratio(" ".join(line.text for line in text_page.lines))
            if ratio > MAX_GARBLED:
                # Garbled text layer: extracting from it would give wrong values and evidence; use
                # OCR instead.
                scanned = _render_and_ocr(path, number, ocr)
                note = stored(
                    "PDF 文字层是乱码（{ratio:.0%} 的字认不出来），这一页改用 OCR", ratio=ratio
                )
                pages.append(replace(scanned, models={**scanned.models, "note": note}))
            else:
                pages.append(text_page)
    return pages


def _text_layer(page: Page) -> OcrPage | None:
    """Read the embedded text with coordinates. Returns None when there is too little text (usually
    a scan)."""
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
        # Some PDFs encode CJK ideographs as radical code points (⽰, ⼩, ⻔) that look identical
        # but copy differently; normalize them first.
        lines.append(OcrLine(text=plain_cjk(text), polygon=polygon, confidence=1.0))

    return OcrPage(
        width=round(page.width * PDF_SCALE),
        height=round(page.height * PDF_SCALE),
        lines=tuple(lines),
        engine="pdf-text",
        models={"engine": f"pdfplumber {version('pdfplumber')}", "source": stored("PDF 文字层")},
        duration_ms=round((time.perf_counter() - start) * 1000),
    )


PHRASE_GAP = 0.6
"""Two words on the same line separated by at most text height × 0.6 (about one or two spaces) form
one span ("Invoice Number:"); wider gaps separate columns ("Check-in date" and "Rooms" in a
table), so evidence boxes do not stretch across the line."""


def _phrases(words: list[dict[str, Any]]) -> list[tuple[str, float, float, float, float]]:
    """PDF words → text spans: nearby words on the same line are joined; distant words are
    separated.

        Previously pdfplumber's keep_blank_chars merged them, and PDFs that align with many
    spaces joined whole lines into one block, so evidence boxes stretched from the shop name to
    the room count. Here words are split by their actual gaps; words ending with a colon ("Guest
    Name:") are also split from what follows, which is the value. Returns (text, x0, top, x1,
    bottom) in PDF points."""
    rows: list[list[dict[str, Any]]] = []
    for w in sorted(words, key=lambda w: (round(float(w["top"])), float(w["x0"]))):
        if not str(w["text"]).strip():
            continue
        height = float(w["bottom"]) - float(w["top"])
        middle = (float(w["top"]) + float(w["bottom"])) / 2
        for row in rows:
            last = row[-1]
            last_middle = (float(last["top"]) + float(last["bottom"])) / 2
            same_top = abs(float(last["top"]) - float(w["top"])) <= max(1.0, 0.4 * height)
            # Glyph boxes vary in height (the box of "一" is a single stroke tall): alignment by
            # center line also counts as the same line.
            same_middle = abs(last_middle - middle) <= max(1.0, 0.25 * height)
            if same_top or same_middle:
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
                height = max(
                    float(prev["bottom"]) - float(prev["top"]),
                    float(w["bottom"]) - float(w["top"]),
                    1.0,
                )
                wide = float(w["x0"]) - float(prev["x1"]) > PHRASE_GAP * height
                # "Guest Name: Sanjay": the text after the colon is the value; split it from the
                # label so the evidence box covers only the value.
                label = str(prev["text"]).rstrip().endswith((":", "："))
                if wide or label:
                    phrases.append(_phrase(current))
                    current = []
            current.append(w)
        if current:
            phrases.append(_phrase(current))
    return phrases


def _joined(words: list[dict[str, Any]]) -> str:
    """Join words into a span: no space between two CJK characters ("示例市第" + "一" →
    "示例市第一")."""
    text = ""
    for w in words:
        piece = str(w["text"]).strip()
        if text and not (_cjk(text[-1]) and _cjk(piece[:1])):
            text += " "
        text += piece
    return text


def _cjk(char: str) -> bool:
    return bool(char) and (
        "\u2e80" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff" or "\uff00" <= char <= "\uffef"
    )


def _phrase(words: list[dict[str, Any]]) -> tuple[str, float, float, float, float]:
    text = _joined(words)
    return (
        text,
        min(float(w["x0"]) for w in words),
        min(float(w["top"]) for w in words),
        max(float(w["x1"]) for w in words),
        max(float(w["bottom"]) for w in words),
    )


def _render_and_ocr(path: Path, number: int, ocr: OcrEngine) -> OcrPage:
    """Render a PDF page to an image and run OCR."""
    with PDFIUM_LOCK:
        pdf = open_pdf(path)
        try:
            image = pdf[number].render(scale=PDF_SCALE, may_draw_forms=True).to_pil()
        finally:
            pdf.close()
    with tempfile.TemporaryDirectory() as folder:
        image_path = Path(folder) / f"page-{number + 1}.png"
        image.save(image_path)
        return ocr.recognize(image_path)


def page_images(path: Path) -> list[np.ndarray]:
    """Render every page of the file as an image (BGR array for OpenCV) for the layout and table
    models.

        Coordinates match OCR: PDFs are rendered at PDF_SCALE (pages with a text layer too, whose
    coordinates use the same scale); images are rotated according to EXIF orientation."""
    suffix = path.suffix.lower()
    pictures: list[Image.Image] = []
    if suffix == ".pdf":
        with PDFIUM_LOCK:
            pdf = open_pdf(path)
            try:
                pictures = [
                    page.render(scale=PDF_SCALE, may_draw_forms=True).to_pil() for page in pdf
                ]
            finally:
                pdf.close()
    elif suffix in IMAGE_SUFFIXES:
        with Image.open(path) as picture:
            pictures = [ImageOps.exif_transpose(picture).copy()]
    return [np.ascontiguousarray(np.asarray(p.convert("RGB"))[:, :, ::-1]) for p in pictures]


def page_png(data: bytes, is_pdf: bool, number: int, scale: float = PDF_SCALE) -> bytes:
    """Render page number (1-based) of the file as PNG for the review page.

    Coordinates must equal those used during OCR so evidence boxes are drawn precisely:
    - PDFs are rendered at the same PDF_SCALE (text-layer coordinates use it too), including
      forms; scale may be larger for zoomed re-recognition, in which case coordinates are
      multiplied by scale / PDF_SCALE;
    - Images are first rotated by EXIF orientation: phone photos are often stored sideways
      with a "rotate 90 degrees" tag, and RapidOCR rotates before recognition, so coordinates
      refer to the rotated image. Output is normalized to PNG since browsers cannot display
      TIFF.
    Raises IndexError for out-of-range page numbers; callers check first."""
    if is_pdf:
        with PDFIUM_LOCK:
            pdf = open_pdf(data)
            try:
                if not 1 <= number <= len(pdf):
                    raise IndexError(number)
                image = pdf[number - 1].render(scale=scale, may_draw_forms=True).to_pil()  # pyright: ignore[reportArgumentType]
            finally:
                pdf.close()
    else:
        if number != 1:
            raise IndexError(number)
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if image.mode not in ("RGB", "RGBA", "L"):
            image = image.convert("RGB")  # Convert CMYK, palette and other modes to RGB.
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
