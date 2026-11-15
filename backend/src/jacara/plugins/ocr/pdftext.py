"""Read the text layer and form fields of a PDF page with pdfium, converting coordinates to pixels
of the rendered image.

Why not pdfplumber: invisible text (render mode 3, an old OCR layer left on scans by other
software) must be told apart, and pdfplumber cannot read the render mode. pdfium can, and its
coordinate conversion (PdfPosConv) is the same one used for rendering, so rotated pages align
too."""

from __future__ import annotations

import ctypes
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from jacara.plugins.ocr.base import FormValue, OcrLine, Point

_INVISIBLE = (3, 7)
"""Render modes 3 (invisible) and 7 (clip only): the text is in the text layer but not visible on
the page."""

_HIDDEN_FLAGS = 1 | 2 | 32
"""Widget flags: 1 invisible, 2 hidden, 32 no view (print only)."""


def _box(conv: Any, left: float, bottom: float, right: float, top: float) -> tuple[float, ...]:
    """Box in PDF coordinates → box (x0, y0, x1, y1) on the rendered image. All four corners are
    converted, so rotated pages are correct."""
    corners = [
        conv.to_bitmap(x, y)
        for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def text_words(page: pdfium.PdfPage, conv: Any) -> tuple[list[dict[str, Any]], int]:
    """Visible words in this page's text layer: [{text, x0, top, x1, bottom}] (pixels of the
    rendered image), and the number of invisible characters."""
    textpage = page.get_textpage()
    try:
        count = raw.FPDFText_CountChars(textpage.raw)
        words: list[dict[str, Any]] = []
        current: list[tuple[str, tuple[float, ...]]] = []
        hidden = 0

        def flush() -> None:
            if current:
                boxes = [b for _, b in current]
                words.append(
                    {
                        "text": "".join(c for c, _ in current),
                        "x0": min(b[0] for b in boxes),
                        "top": min(b[1] for b in boxes),
                        "x1": max(b[2] for b in boxes),
                        "bottom": max(b[3] for b in boxes),
                    }
                )
                current.clear()

        rect = raw.FS_RECTF()
        for i in range(count):
            char = chr(raw.FPDFText_GetUnicode(textpage.raw, i))
            obj = raw.FPDFText_GetTextObject(textpage.raw, i)
            if not obj or char.isspace() or raw.FPDFText_IsGenerated(textpage.raw, i) == 1:
                flush()  # Space, newline or a separator inserted by pdfium: end of a word.
                continue
            if raw.FPDFTextObj_GetTextRenderMode(obj) in _INVISIBLE:
                hidden += 1
                flush()
                continue
            # Loose box (font ascent and descent, character advance): adjacent characters touch end
            # to end and are not split by mistake.
            raw.FPDFText_GetLooseCharBox(textpage.raw, i, rect)
            box = _box(conv, rect.left, rect.bottom, rect.right, rect.top)
            if current and _gap(current[-1][1], box) > 0.5 * min(_size(box), _size(current[-1][1])):
                flush()  # Far apart within the same object (layout spacing): split too.
            current.append((char, box))
        flush()
        return words, hidden
    finally:
        textpage.close()


def _size(box: tuple[float, ...]) -> float:
    """Character size: the shorter side of the box (height for horizontal text, width for vertical
    text and pages rotated 90 degrees)."""
    return max(1.0, min(box[2] - box[0], box[3] - box[1]))


def _gap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Gap between two characters (the larger of horizontal and vertical; negative when
    overlapping)."""
    return max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3])


def form_values(document: pdfium.PdfDocument, page: pdfium.PdfPage, conv: Any) -> list[FormValue]:
    """Values stored in the form fields of this page (text fields, combo boxes, …).
    document.init_forms() must have been called."""
    env = document.formenv
    if env is None:
        return []
    found: list[FormValue] = []
    for k in range(raw.FPDFPage_GetAnnotCount(page.raw)):
        annot = raw.FPDFPage_GetAnnot(page.raw, k)
        try:
            if raw.FPDFAnnot_GetSubtype(annot) != raw.FPDF_ANNOT_WIDGET:
                continue
            name = _utf16(raw.FPDFAnnot_GetFormFieldName, env.raw, annot)
            value = _utf16(raw.FPDFAnnot_GetFormFieldValue, env.raw, annot)
            if not value.strip():
                continue
            rect = raw.FS_RECTF()
            raw.FPDFAnnot_GetRect(annot, rect)
            box = _box(conv, rect.left, rect.bottom, rect.right, rect.top)
            hidden = bool(raw.FPDFAnnot_GetFlags(annot) & _HIDDEN_FLAGS)
            found.append(FormValue(name, value, (box[0], box[1], box[2], box[3]), hidden))
        finally:
            raw.FPDFPage_CloseAnnot(annot)
    return found


def _utf16(call: Any, env: Any, annot: Any) -> str:
    """pdfium functions returning UTF-16 strings: query the size first, then fetch."""
    size = call(env, annot, None, 0)
    if size <= 2:
        return ""
    buf = ctypes.create_string_buffer(size)
    call(env, annot, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), size)
    return buf.raw.decode("utf-16-le").rstrip("\x00")


def as_lines(phrases: list[tuple[str, float, float, float, float]]) -> tuple[OcrLine, ...]:
    """(text, x0, top, x1, bottom) → OcrLine. The text layer has no confidence; 1.0 is recorded."""
    return tuple(
        OcrLine(
            text=text,
            polygon=(Point(x0, top), Point(x1, top), Point(x1, bottom), Point(x0, bottom)),
            confidence=1.0,
        )
        for text, x0, top, x1, bottom in phrases
    )
