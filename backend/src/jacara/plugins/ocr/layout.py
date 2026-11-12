"""把 OCR 的"文字块"按图上的位置合并成"视觉行"。

OCR 给的是一块一块的文字。收据上"Date:"和"25/12/2018"是同一行左右两块，
但 OCR 会把它们当成两条。按位置把它们合回一行，大模型才看得出谁和谁是一对。
抽取器和证据对齐都用这里的行号，所以两边说的"第几行"永远是同一行。

手机拍的收据常常是歪的、弯的。歪的文字块，外接矩形会比字高很多，按矩形的上下重叠来判断，
会把好几行粘成一行（一张歪着拍的收据曾经被粘成几大行，模型完全读不懂）。所以这里按文字块
自己的倾斜方向来判断：顺着一行文字的斜度往左右延长，另一个文字块的中心落在这条线附近，才算同一行。
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from statistics import median

from jacara.plugins.ocr.base import OcrLine, OcrPage

SAME_ROW = 0.5
"""另一个文字块的中心离这一行的延长线不超过"字高 × 0.5"，算同一行。"""

MAX_ANGLE = 0.12
"""两个文字块的倾斜角相差超过这么多（弧度，约 7 度），不算同一行：多半是照片里的另一张纸。"""


@dataclass(frozen=True)
class OcrRow:
    """图上的一行：由一个或多个文字块组成，从左到右排列。index 从 1 开始。"""

    index: int
    lines: tuple[OcrLine, ...]

    @property
    def text(self) -> str:
        """这一行的全部文字，文字块之间用两个空格隔开。"""
        return "  ".join(line.text for line in self.lines)


@dataclass(frozen=True)
class _Geo:
    """一个文字块的几何信息：中心、字高、倾斜度（每向右 1 像素，往下几像素）。"""

    line: OcrLine
    cx: float
    cy: float
    height: float
    width: float
    slope: float | None
    """文字块太短（不到字高的 2 倍宽）时，斜度不准，是 None，改用附近文字块的斜度。"""


def _geo(line: OcrLine) -> _Geo:
    tl, tr, br, bl = line.polygon
    lx, ly = (tl.x + bl.x) / 2, (tl.y + bl.y) / 2
    rx, ry = (tr.x + br.x) / 2, (tr.y + br.y) / 2
    height = (math.dist((tl.x, tl.y), (bl.x, bl.y)) + math.dist((tr.x, tr.y), (br.x, br.y))) / 2
    width = math.dist((lx, ly), (rx, ry))
    dx = rx - lx
    slope = (ry - ly) / dx if dx > 0 and width > 2 * height else None
    return _Geo(line, (lx + rx) / 2, (ly + ry) / 2, max(height, 1.0), width, slope)


def group_rows(page: OcrPage, same_row: float = SAME_ROW) -> tuple[OcrRow, ...]:
    """把文字块合并成行，从上到下编号。

    新的文字块和每一行里横向最近的块比：顺着两边的斜度延长过去，中心离延长线都不超过
    字高 × same_row，而且斜度差不多，就加入这一行；离哪一行都不够近，就自己开一行。
    """
    geos = [_geo(line) for line in page.lines]
    if not geos:
        return ()
    reliable = sorted((g for g in geos if g.slope is not None), key=lambda g: g.cy)
    page_slope = median(g.slope for g in reliable if g.slope is not None) if reliable else 0.0

    def local_slope(g: _Geo) -> float:
        """短文字块用附近（上下最近的 5 个）长文字块的斜度：收据弯了，各处斜度不一样。"""
        if g.slope is not None:
            return g.slope
        near = sorted(reliable, key=lambda r: abs(r.cy - g.cy))[:5]
        return median(r.slope for r in near if r.slope is not None) if near else page_slope

    slopes = {id(g): local_slope(g) for g in geos}

    def slope_of(g: _Geo) -> float:
        return slopes[id(g)]

    def y_at(anchor: _Geo, x: float) -> float:
        """顺着 anchor 的斜度，在横坐标 x 处的高度。"""
        return anchor.cy + slope_of(anchor) * (x - anchor.cx)

    def gap(a: _Geo, b: _Geo) -> float | None:
        """两个文字块离"在同一行"差多远（像素）。斜度明显不同，就不可能同一行，返回 None。

        两边都算一次：b 离 a 的延长线多远、a 离 b 的延长线多远，取大的。
        """
        if abs(math.atan(slope_of(a)) - math.atan(slope_of(b))) > MAX_ANGLE:
            return None
        return max(abs(b.cy - y_at(a, b.cx)), abs(a.cy - y_at(b, a.cx)))

    rows: list[list[_Geo]] = []
    for g in sorted(geos, key=lambda g: g.cy - page_slope * g.cx):
        best: tuple[float, int] | None = None
        for i, row in enumerate(rows):
            # 和这一行里横向离得最近的文字块比：收据是弯的，离得远的块延长过来误差大
            near = min(row, key=lambda m: abs(m.cx - g.cx))
            d = gap(near, g)
            if d is None or d > same_row * min(g.height, near.height):
                continue
            if best is None or d < best[0]:
                best = (d, i)
        if best is None:
            rows.append([g])
        else:
            rows[best[1]].append(g)

    # 从上到下：比较每一行在页面正中间的高度（用这一行最宽的块延长过去），歪着拍的收据也排得对
    middle = page.width / 2
    order = sorted(
        range(len(rows)), key=lambda i: y_at(max(rows[i], key=lambda m: m.width), middle)
    )
    return tuple(
        OcrRow(index=n, lines=tuple(g.line for g in sorted(rows[i], key=lambda g: g.cx)))
        for n, i in enumerate(order, start=1)
    )


CONTINUE_MARK = "↳"
"""给模型看的文字里，"按位置看可能接着上一行"的行，行号后面加这个记号。"""

MAX_LINE_GAP = 0.8
"""上下两行之间的空隙不超过"字高 × 0.8"，才可能是同一段文字折行（再大就像空了一行）。"""

HEIGHT_RATIO = 1.45
"""两行的字高相差超过这个倍数，不算同一段（标题和正文、大字和小字）。"""

_LABEL = re.compile(r"^[^\s:：]{1,24}\s*[:：]")
"""以"名称："开头的行（电话：、Date:），是另起的一项，不是接着上一行写的。"""
_WORDY = re.compile(r"[A-Za-z\u4e00-\u9fff]")


def _box(line: OcrLine) -> tuple[float, float, float, float]:
    xs = [p.x for p in line.polygon]
    ys = [p.y for p in line.polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _row_height(row: OcrRow) -> float:
    return median(_geo(line).height for line in row.lines)


def continues(above: OcrRow, below: OcrRow) -> bool:
    """按位置看，below 是不是接着 above 写的（同一段文字折行，例如商品名、地址写成两行）。

    同时满足才算：
    - 上下挨得很近：空隙不超过字高 × MAX_LINE_GAP；
    - 字一样大：字高相差不超过 HEIGHT_RATIO 倍；
    - 左边对齐：below 的第一块和 above 的某一块左边对齐（差不到 1.5 个字高）。
      表格里商品名折行，第二行只在商品名那一列下面有字；
    - below 的第一块是文字（不是金额、数量），也不是"名称："开头的另一项。

    这只是线索：是不是同一个值，由模型决定（例如店名下面一行是地址，位置上是"接着写的"，
    但不属于店名），再由证据对齐核对。
    """
    h_above, h_below = _row_height(above), _row_height(below)
    h = min(h_above, h_below)
    if max(h_above, h_below) > HEIGHT_RATIO * h:
        return False
    bottom = max(_box(line)[3] for line in above.lines)
    top = min(_box(line)[1] for line in below.lines)
    if not -0.3 * h <= top - bottom <= MAX_LINE_GAP * h:
        return False
    first = below.lines[0]
    if not _WORDY.search(first.text) or _LABEL.match(below.text):
        return False
    left = _box(first)[0]
    return any(abs(_box(line)[0] - left) <= 1.5 * h for line in above.lines)


def continuation_rows(rows: tuple[OcrRow, ...]) -> frozenset[int]:
    """哪些行（行号）按位置看是接着上一行写的。"""
    return frozenset(
        below.index for above, below in itertools.pairwise(rows) if continues(above, below)
    )


def numbered_text(rows: tuple[OcrRow, ...], marks: frozenset[int] = frozenset()) -> str:
    """给大模型看的文字：每行前面加上 [行号]；marks 里的行（可能接着上一行）行号后面加 ↳。"""
    return "\n".join(
        f"[{row.index}]{CONTINUE_MARK if row.index in marks else ''} {row.text}" for row in rows
    )


# ---- 列标题：上面一行是几个标题，下面一行（或几行）按列对齐写值 ----

HEADER_GAP = 1.5
"""标题行和值所在行之间的空隙不超过"字高 × 1.5"。"""

_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class HeaderColumn:
    """一列：标题、按列对应的值（几行连起来）、值在第几行到第几行。"""

    label: str
    value: str
    first_row: int
    last_row: int


@dataclass(frozen=True)
class HeaderGroup:
    """一组"列标题 + 值"：header_row 是标题所在的行。"""

    header_row: int
    columns: tuple[HeaderColumn, ...]


def _label_like(text: str) -> bool:
    """像标题：有字、没有数字、不长（"Check In"、"Booking ID"、"房间数"）。"""
    return bool(_WORDY.search(text)) and not _DIGIT.search(text) and len(text) <= 40


def _column_of(line: OcrLine, headers: tuple[OcrLine, ...], h: float) -> int | None:
    """这块值在哪个标题下面：左边和标题对齐（差不到 1.5 个字高），或者中心落在标题的左右范围里。"""
    x0, _, x1, _ = _box(line)
    cx = (x0 + x1) / 2
    best: tuple[float, int] | None = None
    for i, header in enumerate(headers):
        hx0, _, hx1, _ = _box(header)
        if abs(x0 - hx0) <= 1.5 * h or hx0 <= cx <= hx1:
            d = abs(x0 - hx0)
            if best is None or d < best[0]:
                best = (d, i)
    return None if best is None else best[1]


def header_groups(rows: tuple[OcrRow, ...]) -> tuple[HeaderGroup, ...]:
    """找出"上面一行是标题、下面按列写值"的地方。

    酒店账单、发票抬头常这样排：
        Hotel Details            Check In     Check Out    Rooms
        OYO 4189 Resort ...,     31/12/2017   01/01/2018   1
        25, Vembuliamman ...（地址，接着上一行写）
    变成一行一行的文字以后，"1 在 Rooms 下面"这层关系就没了，模型只能猜。这里按位置把它对出来：
    - 标题行至少 2 块，每块都像标题（有字、没有数字）；
    - 下一行紧挨着（空隙不超过字高 × HEADER_GAP），至少 2 块分别对齐到不同的标题，
      并且至少六成的块对得上；
    - 再往下"接着上一行写"的行（continues），对得上哪一列就接到哪一列后面。
    """
    groups: list[HeaderGroup] = []
    for n, (above, below) in enumerate(itertools.pairwise(rows)):
        headers = above.lines
        if len(headers) < 2 or not all(_label_like(h.text) for h in headers):
            continue
        h = min(_row_height(above), _row_height(below))
        gap = min(_box(ln)[1] for ln in below.lines) - max(_box(ln)[3] for ln in above.lines)
        if not -0.3 * h <= gap <= HEADER_GAP * h:
            continue
        found = [_column_of(ln, headers, h) for ln in below.lines]
        hit = [c for c in found if c is not None]
        if len(set(hit)) < 2 or len(hit) < 0.6 * len(below.lines):
            continue
        texts: dict[int, list[str]] = {}
        rows_of: dict[int, list[int]] = {}
        for line, col in zip(below.lines, found, strict=True):
            if col is not None:
                texts.setdefault(col, []).append(line.text)
                rows_of.setdefault(col, []).append(below.index)
        previous = below
        for nxt in rows[n + 2 :]:
            if not continues(previous, nxt):
                break
            for line in nxt.lines:
                col = _column_of(line, headers, h)
                if col is not None and col in texts:
                    texts[col].append(line.text)
                    rows_of[col].append(nxt.index)
            previous = nxt
        columns = tuple(
            HeaderColumn(
                label=headers[col].text,
                value=" ".join(texts[col]),
                first_row=min(rows_of[col]),
                last_row=max(rows_of[col]),
            )
            for col in sorted(texts)
        )
        groups.append(HeaderGroup(header_row=above.index, columns=columns))
    return tuple(groups)


def layout_hints(rows: tuple[OcrRow, ...]) -> str:
    """给模型看的"版面提示"：每组列标题一行，写明哪个值在哪个标题下面。没有就是空字符串。"""
    lines: list[str] = []
    for group in header_groups(rows):
        pairs = "；".join(
            f'{c.label} = "{c.value}"'
            + (
                f"（第 {c.first_row}～{c.last_row} 行）"
                if c.last_row != c.first_row
                else f"（第 {c.first_row} 行）"
            )
            for c in group.columns
        )
        lines.append(f"第 {group.header_row} 行是列标题，下面按列对应：{pairs}")
    return "\n".join(lines)
