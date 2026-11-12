"""一个文件里有几张单据（ADR 0036）：把页分成一段一段，每段是一张单据。

四种分法（documents.split）：
- auto：自动判断。先看页上有没有"Page 2 of 3""第 2 页 共 3 页""续"这类标记；
  没有标记，再让模型看上一页结尾和这一页开头，判断是不是续页。没有模型（或模型答不上来）就当新单据。
- one：整个文件是一张单据。
- pages：每页一张。
- 页码范围："1-2, 3, 4-6"。没写到的页按自动判断接在后面。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Literal

from jacara.plugins.ocr.base import OcrPage

Judge = Callable[[OcrPage, OcrPage], bool | None]
"""（上一页，这一页）→ 这一页是不是续页。None 表示判断不了。"""

Segment = tuple[int, int]
"""一张单据：从第几页到第几页（从 1 开始，含两头）。"""


class SplitError(ValueError):
    """页码范围写错了。说明直接给人看。"""


_RANGE = re.compile(r"^\s*(\d+)\s*(?:[-–~]\s*(\d+))?\s*$")


def parse_ranges(text: str) -> list[Segment]:
    """ "1-2, 3, 4-6" → [(1, 2), (3, 3), (4, 6)]。要从 1 开始、按顺序、不重叠。"""
    ranges: list[Segment] = []
    for piece in re.split(r"[,，;；]", text):
        if not piece.strip():
            continue
        m = _RANGE.match(piece)
        if m is None:
            raise SplitError(f"页码写得不对：「{piece.strip()}」，例如 1-2, 3, 4-6")
        start, end = int(m[1]), int(m[2] or m[1])
        expected = ranges[-1][1] + 1 if ranges else 1
        if start != expected or end < start:
            raise SplitError(
                f"页码要从 1 开始、按顺序、不重叠：「{piece.strip()}」应该从第 {expected} 页开始"
            )
        ranges.append((start, end))
    if not ranges:
        raise SplitError("没有写页码")
    return ranges


def normalize_split(split: str | None) -> str:
    """检查并整理写法：auto、one、pages 原样；页码范围整理成 "1-2, 3, 4-6"。"""
    text = (split or "auto").strip()
    if text in ("auto", "one", "pages"):
        return text
    return ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in parse_ranges(text))


_OF = (
    re.compile(r"(?i)\bpage\s*(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b"),
    re.compile(r"第\s*(\d{1,3})\s*页\s*[,，/、]?\s*共\s*(\d{1,3})\s*页"),
    re.compile(r"共\s*(\d{1,3})\s*页\s*[,，/、]?\s*第\s*(\d{1,3})\s*页"),
    re.compile(r"(?i)^\s*-?\s*(\d{1,3})\s*/\s*(\d{1,3})\s*-?\s*$"),
)
_CONTINUED = re.compile(
    r"(?i)\(\s*cont(?:inued|'d)?\.?\s*\)|\bcontinued\b|续页|（续）|\(续\)|承上页"
)

Marker = Literal["starts", "continues"]


def page_marker(page: OcrPage) -> Marker | None:
    """页上写着的标记：第 1 页 → starts；第 2 页及以后、"续""continued" → continues。
    没有标记是 None。"""
    for line in page.lines:
        text = line.text
        for k, pattern in enumerate(_OF):
            m = pattern.search(text)
            if m is None:
                continue
            number = int(m[2]) if k == 2 else int(m[1])
            total = int(m[1]) if k == 2 else int(m[2])
            if 1 <= number <= total:
                return "starts" if number == 1 else "continues"
        if _CONTINUED.search(text):
            return "continues"
    return None


def segments(
    pages: Sequence[OcrPage],
    split: str,
    judge: Judge | None = None,
    progress: Callable[[int], None] | None = None,
) -> list[Segment]:
    """按 split 把页分成几张单据。progress(第几页) 在自动判断每一页前报告一次。"""
    count = len(pages)
    if count == 0:
        return []
    if split == "one":
        return [(1, count)]
    if split == "pages":
        return [(k, k) for k in range(1, count + 1)]
    fixed: list[Segment] = []
    if split != "auto":
        fixed = [(a, min(b, count)) for a, b in parse_ranges(split) if a <= count]
    first = fixed[-1][1] + 1 if fixed else 1
    found = list(fixed)
    for k in range(first, count + 1):
        if k == first:
            found.append((k, k))
            continue
        if progress is not None:
            progress(k)
        if _continues(pages[k - 2], pages[k - 1], judge):
            found[-1] = (found[-1][0], k)
        else:
            found.append((k, k))
    return found


def _continues(previous: OcrPage, current: OcrPage, judge: Judge | None) -> bool:
    marker = page_marker(current)
    if marker is not None:
        return marker == "continues"
    if judge is None:
        return False
    return judge(previous, current) is True
