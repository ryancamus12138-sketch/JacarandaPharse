"""用 pdfium 读 PDF 一页上的文字层和表单字段，坐标换成渲染图上的像素（ADR 0042）。

为什么不用 pdfplumber：要分出"看不见的字"（渲染模式 3，扫描件上别的软件留下的旧 OCR），
pdfplumber 读不出渲染模式；pdfium 能，而且它的坐标换算（PdfPosConv）和渲染用的是同一套，
旋转的页面也能对准。
"""

from __future__ import annotations

import ctypes
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from jacara.plugins.ocr.base import FormValue, OcrLine, Point

_INVISIBLE = (3, 7)
"""渲染模式 3（不画）和 7（只当裁剪路径）：字在文字层里，页面上看不见。"""

_HIDDEN_FLAGS = 1 | 2 | 32
"""控件标志：1 不可见、2 隐藏、32 不显示（只打印）。"""


def _box(conv: Any, left: float, bottom: float, right: float, top: float) -> tuple[float, ...]:
    """PDF 坐标的框 → 渲染图上的框 (x0, y0, x1, y1)。四个角都换算，旋转的页面也对。"""
    corners = [
        conv.to_bitmap(x, y)
        for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def text_words(page: pdfium.PdfPage, conv: Any) -> tuple[list[dict[str, Any]], int]:
    """这一页文字层里看得见的词：[{text, x0, top, x1, bottom}]（渲染图上的像素），
    以及看不见的字有几个。"""
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
                flush()  # 空格、换行、pdfium 自己补的分隔：一个词结束
                continue
            if raw.FPDFTextObj_GetTextRenderMode(obj) in _INVISIBLE:
                hidden += 1
                flush()
                continue
            # 宽松的框（按字体的上下高度、字的步进宽度）：相邻的字首尾相接，不会被误拆开
            raw.FPDFText_GetLooseCharBox(textpage.raw, i, rect)
            box = _box(conv, rect.left, rect.bottom, rect.right, rect.top)
            if current and _gap(current[-1][1], box) > 0.5 * min(_size(box), _size(current[-1][1])):
                flush()  # 同一个对象里隔得远（排版用的距离），也分开
            current.append((char, box))
        flush()
        return words, hidden
    finally:
        textpage.close()


def _size(box: tuple[float, ...]) -> float:
    """字的大小：框的短边（横排是高，竖排、旋转 90 度的页是宽）。"""
    return max(1.0, min(box[2] - box[0], box[3] - box[1]))


def _gap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """两个字之间的空隙（横向、纵向取大的；重叠是负数）。"""
    return max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3])


def form_values(document: pdfium.PdfDocument, page: pdfium.PdfPage, conv: Any) -> list[FormValue]:
    """这一页上的表单字段（文本框、下拉框等）存的值。要先调用过 document.init_forms()。"""
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
    """pdfium 返回 UTF-16 字符串的函数：先问要多大，再取。"""
    size = call(env, annot, None, 0)
    if size <= 2:
        return ""
    buf = ctypes.create_string_buffer(size)
    call(env, annot, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), size)
    return buf.raw.decode("utf-16-le").rstrip("\x00")


def as_lines(phrases: list[tuple[str, float, float, float, float]]) -> tuple[OcrLine, ...]:
    """(文字, x0, top, x1, bottom) → OcrLine。文字层没有置信度，记 1.0。"""
    return tuple(
        OcrLine(
            text=text,
            polygon=(Point(x0, top), Point(x1, top), Point(x1, bottom), Point(x0, bottom)),
            confidence=1.0,
        )
        for text, x0, top, x1, bottom in phrases
    )
