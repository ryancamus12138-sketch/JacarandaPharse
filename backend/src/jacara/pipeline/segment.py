"""Split a file into documents: pages are grouped into segments, each segment being one document.

Four modes (documents.split):
- auto: detect automatically. First look for markers such as "Page 2 of 3", "第 2 页 共 3 页" or
  "续"; without markers, ask the model whether the page continues the previous one, based on
  the end of the previous page and the start of this one. Without a model (or without an
  answer), a new document starts.
- one: the whole file is one document.
- pages: one document per page.
- Page ranges: "1-2, 3, 4-6". Pages not covered are appended using automatic detection."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Literal

from jacara.core.i18n import stored
from jacara.plugins.ocr.base import OcrPage

Judge = Callable[[OcrPage, OcrPage], bool | None]
"""(previous page, this page) → whether this page is a continuation. None if undecidable."""

Segment = tuple[int, int]
"""One document: first and last page (1-based, inclusive)."""


class SplitError(ValueError):
    """Invalid page ranges. The message is shown to the user as is."""


_RANGE = re.compile(r"^\s*(\d+)\s*(?:[-–~]\s*(\d+))?\s*$")


def parse_ranges(text: str) -> list[Segment]:
    """ "1-2, 3, 4-6" → [(1, 2), (3, 3), (4, 6)]. Must start at 1, be in order and not overlap."""
    ranges: list[Segment] = []
    for piece in re.split(r"[,，;；]", text):
        if not piece.strip():
            continue
        m = _RANGE.match(piece)
        if m is None:
            raise SplitError(
                stored("页码写得不对：「{piece}」，例如 1-2, 3, 4-6", piece=piece.strip())
            )
        start, end = int(m[1]), int(m[2] or m[1])
        expected = ranges[-1][1] + 1 if ranges else 1
        if start != expected or end < start:
            raise SplitError(
                stored(
                    "页码要从 1 开始、按顺序、不重叠：「{piece}」应该从第 {expected} 页开始",
                    piece=piece.strip(),
                    expected=expected,
                )
            )
        ranges.append((start, end))
    if not ranges:
        raise SplitError(stored("没有写页码"))
    return ranges


def normalize_split(split: str | None) -> str:
    """Validate and normalize: auto, one and pages unchanged; page ranges normalized to "1-2, 3,
    4-6"."""
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
    """Marker printed on the page: page 1 → starts; page 2 or later, "续", "continued" → continues.
    None without a marker."""
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
    """Split pages into documents according to split. progress(page) is called before each page
    during automatic detection."""
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
