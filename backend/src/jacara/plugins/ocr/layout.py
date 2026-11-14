"""Merge OCR text blocks into visual lines by their position on the image.

OCR returns text in blocks. On a receipt, "Date:" and "25/12/2018" are two blocks on the same
line, yet OCR reports them separately. Merging them back into one line by position lets the LLM
see which belong together. The extractor and evidence alignment share these line numbers, so
"line n" always refers to the same line on both sides.

Receipts photographed with a phone are often skewed or curved. The bounding rectangle of a skewed
block is much taller than its text, so judging by vertical overlap of rectangles glues several
lines together (a skewed receipt was once merged into a few large lines the model could not read
at all). Instead, the slope of each block is used: a line is extended left and right along its
slope, and another block belongs to it only if its center lies near that extension."""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from statistics import median

from jacara.plugins.ocr.base import OcrLine, OcrPage

SAME_ROW = 0.5
"""A block whose center is within text height × 0.5 of the line's extension belongs to the same
line."""

MAX_ANGLE = 0.12
"""Blocks whose slopes differ by more than this (radians, about 7 degrees) are not on the same line:
usually another sheet of paper in the photo."""


@dataclass(frozen=True)
class OcrRow:
    """A line on the image: one or more text blocks ordered left to right. index starts at 1."""

    index: int
    lines: tuple[OcrLine, ...]

    @property
    def text(self) -> str:
        """All text of this line, blocks separated by two spaces."""
        return "  ".join(line.text for line in self.lines)


@dataclass(frozen=True)
class _Geo:
    """Geometry of a text block: center, text height, slope (pixels down per pixel to the right)."""

    line: OcrLine
    cx: float
    cy: float
    height: float
    width: float
    slope: float | None
    """None for blocks too short (narrower than twice the text height) for a reliable slope; the
    slope of nearby blocks is used instead."""


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
    """Merge text blocks into lines numbered top to bottom.

        Each new block is compared with the horizontally nearest block of each line: extended
    along both slopes, if both centers are within text height × same_row of the extensions and
    the slopes are similar, the block joins that line; if no line is close enough, it starts a
    new one."""
    geos = [_geo(line) for line in page.lines]
    if not geos:
        return ()
    reliable = sorted((g for g in geos if g.slope is not None), key=lambda g: g.cy)
    page_slope = median(g.slope for g in reliable if g.slope is not None) if reliable else 0.0

    def local_slope(g: _Geo) -> float:
        """Short blocks use the slope of nearby long blocks (the 5 nearest above and below): curved
        receipts have different slopes in different places."""
        if g.slope is not None:
            return g.slope
        near = sorted(reliable, key=lambda r: abs(r.cy - g.cy))[:5]
        return median(r.slope for r in near if r.slope is not None) if near else page_slope

    slopes = {id(g): local_slope(g) for g in geos}

    def slope_of(g: _Geo) -> float:
        return slopes[id(g)]

    def y_at(anchor: _Geo, x: float) -> float:
        """Height at x along the slope of anchor."""
        return anchor.cy + slope_of(anchor) * (x - anchor.cx)

    def gap(a: _Geo, b: _Geo) -> float | None:
        """Distance (pixels) of two blocks from being on the same line. None if their slopes clearly
        differ, ruling out the same line.

                Computed both ways (b from a's extension and a from b's extension); the larger is
        returned."""
        if abs(math.atan(slope_of(a)) - math.atan(slope_of(b))) > MAX_ANGLE:
            return None
        return max(abs(b.cy - y_at(a, b.cx)), abs(a.cy - y_at(b, a.cx)))

    rows: list[list[_Geo]] = []
    for g in sorted(geos, key=lambda g: g.cy - page_slope * g.cx):
        best: tuple[float, int] | None = None
        for i, row in enumerate(rows):
            # Compare with the horizontally nearest block of the line: on curved receipts, extending
            # distant blocks accumulates error.
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

    # Top to bottom: compare each line's height at the horizontal center of the page (extending its
    # widest block), so skewed photos sort correctly too.
    middle = page.width / 2
    order = sorted(
        range(len(rows)), key=lambda i: y_at(max(rows[i], key=lambda m: m.width), middle)
    )
    return tuple(
        OcrRow(index=n, lines=tuple(g.line for g in sorted(rows[i], key=lambda g: g.cx)))
        for n, i in enumerate(order, start=1)
    )


CONTINUE_MARK = "↳"
"""Marker appended to the line number, in the text shown to the model, for lines that may continue
the previous line by position."""

MAX_LINE_GAP = 0.8
"""The gap between two lines must be at most text height × 0.8 for them to be one wrapped paragraph
(larger gaps look like an empty line)."""

HEIGHT_RATIO = 1.45
"""Lines whose text heights differ by more than this factor are not one paragraph (heading and body,
large and small text)."""

_LABEL = re.compile(r"^[^\s:：]{1,24}\s*[:：]")
"""Lines starting with "label:" (电话：, Date:) begin a new item rather than continuing the previous
line."""
_WORDY = re.compile(r"[A-Za-z\u4e00-\u9fff]")


def _box(line: OcrLine) -> tuple[float, float, float, float]:
    xs = [p.x for p in line.polygon]
    ys = [p.y for p in line.polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _row_height(row: OcrRow) -> float:
    return median(_geo(line).height for line in row.lines)


def continues(above: OcrRow, below: OcrRow) -> bool:
    """Whether below continues above by position (one value wrapped onto two lines, such as a
    product name or address).

        All of the following must hold:
        - The lines are close: gap at most text height × MAX_LINE_GAP;
        - Same text size: heights differ by at most HEIGHT_RATIO;
        - Left-aligned: the first block of below aligns with the left edge of some block of above
          (within 1.5 text heights). When a product name wraps in a table, the second line only
          has text under the name column;
        - The first block of below is text (not an amount or quantity) and not another "label:"
          item.

        This is only a hint: whether it is the same value is decided by the model (for example
    the address under a shop name continues it by position but is not part of the name), and
    evidence alignment verifies it."""
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
    """Line numbers of lines that continue the previous line by position."""
    return frozenset(
        below.index for above, below in itertools.pairwise(rows) if continues(above, below)
    )


def numbered_text(rows: tuple[OcrRow, ...], marks: frozenset[int] = frozenset()) -> str:
    """Text for the LLM: each line prefixed with [line number]; lines in marks (possible
    continuations) get ↳ after the number."""
    return "\n".join(
        f"[{row.index}]{CONTINUE_MARK if row.index in marks else ''} {row.text}" for row in rows
    )


# ---- Column headers: a line of headers, with values aligned in columns on the line(s) below ----

HEADER_GAP = 1.5
"""Maximum gap between a header line and its value line: text height × 1.5."""

_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class HeaderColumn:
    """A column: header, the values below it (several lines joined), and the line range of the
    values."""

    label: str
    value: str
    first_row: int
    last_row: int


@dataclass(frozen=True)
class HeaderGroup:
    """A group of column headers with values: header_row is the line of the headers."""

    header_row: int
    columns: tuple[HeaderColumn, ...]


def _label_like(text: str) -> bool:
    """Looks like a header: has letters, no digits, short ("Check In", "Booking ID", "房间数")."""
    return bool(_WORDY.search(text)) and not _DIGIT.search(text) and len(text) <= 40


def _column_of(line: OcrLine, headers: tuple[OcrLine, ...], h: float) -> int | None:
    """Header under which this value block sits: left-aligned with the header (within 1.5 text
    heights), or its center within the header's horizontal extent."""
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
    """Find places where a line of headers is followed by values written in columns below.

        Hotel bills and invoice headers are often laid out like this:
            Hotel Details            Check In     Check Out    Rooms
            OYO 4189 Resort ...,     31/12/2017   01/01/2018   1
            25, Vembuliamman ... (address, continuing the line above)
        Once flattened into lines of text, the relation "1 is under Rooms" is lost and the model
    can only guess. It is reconstructed here by position:
        - The header line has at least 2 blocks, each looking like a header (letters, no digits);
        - The next line follows closely (gap at most text height × HEADER_GAP), with at least 2
          blocks aligned to different headers and at least 60% of blocks aligned;
        - Further lines that continue the previous line (continues) are appended to whichever
          column they align with."""
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
    """Layout hints for the model: one line per header group stating which value sits under which
    header. Empty string if none."""
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
