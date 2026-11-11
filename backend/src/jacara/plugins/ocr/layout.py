"""把 OCR 的"文字块"按图上的位置合并成"视觉行"。

OCR 给的是一块一块的文字。收据上"Date:"和"25/12/2018"是同一行左右两块，
但 OCR 会把它们当成两条。按位置把它们合回一行，大模型才看得出谁和谁是一对。
抽取器和证据对齐都用这里的行号，所以两边说的"第几行"永远是同一行。

手机拍的收据常常是歪的、弯的。歪的文字块，外接矩形会比字高很多，按矩形的上下重叠来判断，
会把好几行粘成一行（一张歪着拍的收据曾经被粘成几大行，模型完全读不懂）。所以这里按文字块
自己的倾斜方向来判断：顺着一行文字的斜度往左右延长，另一个文字块的中心落在这条线附近，才算同一行。
"""

from __future__ import annotations

import math
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


def numbered_text(rows: tuple[OcrRow, ...]) -> str:
    """给大模型看的文字：每行前面加上 [行号]。"""
    return "\n".join(f"[{row.index}] {row.text}" for row in rows)
