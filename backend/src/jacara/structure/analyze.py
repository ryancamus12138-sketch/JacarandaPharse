"""One page of OCR results → page structure: text blocks, lines, layout regions, tables, label →
value pairs.

Steps (following the LAYOUT, TABLES and FORMS features of AWS Textract):
1. WORD, LINE: OCR text blocks are numbered in reading order and merged into visual lines by
   position (plugins/ocr/layout.py).
2. LAYOUT: with a layout model, regions (title, text, table, header, footer, …) are detected and
   the text blocks inside are assigned to them.
3. TABLE / CELL / MERGED_CELL: for each table found by the layout model, a table structure model
   (if present) recognizes rows, columns and merged cells; otherwise cells are derived from text
   positions (one row per visual line; columns from the horizontal projection of the text).
4. KEY / VALUE: Textract finds label → value pairs with trained models. Comparable offline models
   (the LayoutXLM family) are not licensed for commercial use, so layout rules are used instead,
   covering five cases:
   - "label: value" within one block ("Date: 31/12/2017")
   - A label ending with a colon, with the value to its right or directly below ("Guest Name:"
     "Sanjay")
   - A line of labels with values in columns below ("Check In  Check Out  Rooms" / "31/12/2017
     01/01/2018  1")
   - Two-column tables whose left column holds labels
   - Exactly two blocks on a line: a label-like block on the left and a number on the right
     ("Grand Total   Rs 1939")
   The result is only structure; the LLM decides which value fills which field, and evidence
alignment verifies it."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

import numpy as np

from jacara.core.i18n import N_c
from jacara.plugins.ocr.base import OcrLine, OcrPage
from jacara.plugins.ocr.layout import OcrRow, continues, group_rows
from jacara.structure.engines import NO_MODELS, TABLE, CellGuess, Region, StructureEngines
from jacara.structure.graph import Block, Box, PageStructure

_WORDY = re.compile(r"[A-Za-z一-鿿]")
_DIGIT = re.compile(r"\d")
_INLINE = re.compile(r"^(?P<key>[^:：\d]{1,40}?)\s*[:：]\s*(?P<value>\S.*)$")
""""label: value" within one block. The label must not contain digits ("8:13" is not a label and
value)."""

WIDE_GAP = 0.35
"""Maximum horizontal gap between a colon-terminated label and the value to its right: 35% of the
page width."""


def _box(line: OcrLine) -> Box:
    return line.bbox


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _inside(box: Box, area: Box, pad: float = 2.0) -> bool:
    cx, cy = _center(box)
    return area[0] - pad <= cx <= area[2] + pad and area[1] - pad <= cy <= area[3] + pad


def _label_like(text: str) -> bool:
    """Looks like a label: has letters, no digits, short."""
    text = text.strip()
    return bool(_WORDY.search(text)) and not _DIGIT.search(text) and 0 < len(text) <= 40


@dataclass
class _Builder:
    page: OcrPage
    rows: tuple[OcrRow, ...]
    blocks: dict[str, Block]
    words: list[str]
    lines: list[str]
    ocr_lines: list[OcrLine]
    word_of: dict[int, str]
    """id(OcrLine) → WORD id."""

    def add(self, block: Block) -> Block:
        self.blocks[block.id] = block
        return block

    def word_ids(self, lines: Iterable[OcrLine]) -> tuple[str, ...]:
        return tuple(self.word_of[id(ln)] for ln in lines)

    def text(self, ids: Iterable[str]) -> str:
        return " ".join(self.blocks[i].text for i in ids)

    def union(self, ids: Iterable[str]) -> Box:
        boxes = [self.blocks[i].box for i in ids]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


def analyze_page(
    page: OcrPage,
    image: np.ndarray | None = None,
    engines: StructureEngines = NO_MODELS,
    page_starts: tuple[float, ...] = (),
) -> PageStructure:
    """Analyze the structure of one page. image is the page image (same coordinates as OCR); without
    an image no models are used, only rules.

        page_starts: for stacked documents, the y offset at which each page begins; recorded in
    the structure so the model input can mark page breaks."""
    rows = group_rows(page)
    b = _Builder(page, rows, {}, [], [], [], {})
    index_of = {id(ln): i for i, ln in enumerate(page.lines)}

    # 1. WORD, LINE
    for row in rows:
        ids: list[str] = []
        for line in row.lines:
            n = len(b.words) + 1
            word = b.add(
                Block(
                    f"w{n}",
                    "WORD",
                    line.text,
                    _box(line),
                    line.confidence,
                    attrs={"row": row.index, "ocr": index_of[id(line)]},
                )
            )
            b.words.append(word.id)
            b.ocr_lines.append(line)
            b.word_of[id(line)] = word.id
            ids.append(word.id)
        line_id = f"l{row.index}"
        b.add(Block(line_id, "LINE", row.text, b.union(ids), _min_conf(b, ids), tuple(ids)))
        b.lines.append(line_id)

    sources: dict[str, str] = {}
    # 2. LAYOUT
    regions: list[Region] = []
    if image is not None and engines.layout is not None and b.words:
        regions = engines.layout.detect(image)
        sources["layout"] = engines.layout.name
    layouts: list[str] = []
    seen: dict[tuple[str, ...], float] = {}
    kept: list[tuple[Region, tuple[str, ...]]] = []
    # The layout model sometimes reports the same area twice (once as title, once as text): keep
    # only the higher-scoring region for the same blocks.
    for region in sorted(regions, key=lambda r: -r.score):
        inside = tuple(w for w in b.words if _inside(b.blocks[w].box, region.box))
        if inside and inside in seen:
            continue
        seen[inside] = region.score
        kept.append((region, inside))
    for i, (region, inside) in enumerate(
        sorted(kept, key=lambda k: (k[0].box[1], k[0].box[0])), start=1
    ):
        block = b.add(
            Block(
                f"a{i}",
                "LAYOUT",
                b.text(inside),
                region.box,
                region.score,
                inside,
                {"kind": region.kind},
            )
        )
        layouts.append(block.id)

    # 3. TABLE
    tables: list[str] = []
    in_table: set[str] = set()
    for a in layouts:
        region = b.blocks[a]
        if region.attrs.get("kind") != TABLE or len(region.children) < 2:
            continue
        guesses: list[CellGuess] = []
        if image is not None and engines.table is not None:
            guesses = _recognize(image, region.box, engines)
            sources["table"] = engines.table.name
        else:
            sources.setdefault("table", N_c("structure source", "规则"))
        table = _build_table(b, len(tables) + 1, region, guesses)
        if table is not None:
            tables.append(table.id)
            in_table.update(region.children)

    # 4. KEY / VALUE
    keys = _key_values(b, in_table, tables)

    return PageStructure(
        width=float(page.width),
        height=float(page.height),
        blocks=b.blocks,
        words=tuple(b.words),
        lines=tuple(b.lines),
        layouts=tuple(layouts),
        tables=tuple(tables),
        keys=tuple(keys),
        ocr_lines=tuple(b.ocr_lines),
        sources=sources,
        page_starts=page_starts if len(page_starts) > 1 else (),
    )


def _min_conf(b: _Builder, ids: Iterable[str]) -> float:
    return min((b.blocks[i].confidence for i in ids), default=1.0)


# ---- Tables ----


def _recognize(image: np.ndarray, area: Box, engines: StructureEngines) -> list[CellGuess]:
    """Crop the table region for the table model and convert cell boxes back to full-page
    coordinates."""
    if engines.table is None:
        return []
    h, w = image.shape[:2]
    x0, y0 = max(int(area[0]) - 4, 0), max(int(area[1]) - 4, 0)
    x1, y1 = min(int(area[2]) + 4, w), min(int(area[3]) + 4, h)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return []
    crop = np.ascontiguousarray(image[y0:y1, x0:x1])
    return [
        CellGuess(
            c.row,
            c.row_end,
            c.col,
            c.col_end,
            (c.box[0] + x0, c.box[1] + y0, c.box[2] + x0, c.box[3] + y0),
        )
        for c in engines.table.recognize(crop)
    ]


def _bands(spans: list[tuple[int, float, float]], count: int) -> list[tuple[float, float]]:
    """Extent of each row (or column): the union of the top and bottom edges of its non-spanning
    cells; interpolated from neighbors when empty."""
    found: dict[int, tuple[float, float]] = {}
    for index, lo, hi in spans:
        old = found.get(index)
        found[index] = (lo, hi) if old is None else (min(old[0], lo), max(old[1], hi))
    return [found.get(i, (float("nan"), float("nan"))) for i in range(count)]


def _nearest(value: float, bands: list[tuple[float, float]]) -> int:
    best, best_d = 0, float("inf")
    for i, (lo, hi) in enumerate(bands):
        if lo != lo:  # nan: this row has no cells of its own.
            continue
        d = 0.0 if lo <= value <= hi else min(abs(value - lo), abs(value - hi))
        if d < best_d:
            best, best_d = i, d
    return best


def _build_table(b: _Builder, number: int, region: Block, guesses: list[CellGuess]) -> Block | None:
    """One table: determine the rows and columns and the cell of each text block, then generate CELL
    blocks (one per position) and MERGED_CELL blocks."""
    words = list(region.children)
    # Merged cell: (row, last row, column, last column).
    spans: list[tuple[int, int, int, int]] = []
    if guesses:
        n_rows = max(g.row_end for g in guesses) + 1
        n_cols = max(g.col_end for g in guesses) + 1
        row_bands = _bands(
            [(g.row, g.box[1], g.box[3]) for g in guesses if g.row == g.row_end], n_rows
        )
        col_bands = _bands(
            [(g.col, g.box[0], g.box[2]) for g in guesses if g.col == g.col_end], n_cols
        )
        place = {
            w: (
                _nearest(_center(b.blocks[w].box)[1], row_bands),
                _nearest(_center(b.blocks[w].box)[0], col_bands),
            )
            for w in words
        }
        spans = [
            (g.row, g.row_end, g.col, g.col_end)
            for g in guesses
            if g.row_end > g.row or g.col_end > g.col
        ]
        rule = "model"
    else:
        place, n_rows, n_cols = _rule_grid(b, region)
        rule = "rule"
    if n_rows < 1 or n_cols < 2:
        return None

    tid = f"t{number}"
    cell_ids: list[str] = []
    cell_at: dict[tuple[int, int], str] = {}
    for r in range(n_rows):
        for c in range(n_cols):
            inside = tuple(w for w in words if place.get(w) == (r, c))
            cid = f"c{number}-{r + 1}-{c + 1}"
            box = b.union(inside) if inside else region.box
            b.add(
                Block(
                    cid,
                    "CELL",
                    b.text(inside),
                    box,
                    _min_conf(b, inside),
                    inside,
                    {"table": tid, "row": r + 1, "col": c + 1, "empty": not inside},
                )
            )
            cell_ids.append(cid)
            cell_at[(r, c)] = cid
    merged: list[str] = []
    for k, (r0, r1, c0, c1) in enumerate(spans, start=1):
        parts = tuple(
            cell_at[(r, c)]
            for r in range(r0, r1 + 1)
            for c in range(c0, c1 + 1)
            if (r, c) in cell_at
        )
        inside = tuple(w for p in parts for w in b.blocks[p].children)
        mid = f"m{number}-{k}"
        b.add(
            Block(
                mid,
                "MERGED_CELL",
                b.text(inside),
                b.union(inside) if inside else region.box,
                _min_conf(b, inside),
                parts,
                {
                    "table": tid,
                    "row": r0 + 1,
                    "col": c0 + 1,
                    "row_span": r1 - r0 + 1,
                    "col_span": c1 - c0 + 1,
                },
            )
        )
        merged.append(mid)
    return b.add(
        Block(
            tid,
            "TABLE",
            region.text,
            region.box,
            region.confidence,
            tuple(cell_ids),
            {"rows": n_rows, "cols": n_cols, "merged": merged, "by": rule, "layout": region.id},
        )
    )


def _rule_grid(b: _Builder, region: Block) -> tuple[dict[str, tuple[int, int]], int, int]:
    """Without a table model: one row per visual line; the horizontal extents of the text blocks are
    projected together, and each connected range is a column.

        Very wide blocks (over 60% of the table width, such as a note spanning the table) are
    excluded from column detection so they do not merge several columns into one."""
    words = list(region.children)
    width = max(region.box[2] - region.box[0], 1.0)
    narrow = [w for w in words if (b.blocks[w].box[2] - b.blocks[w].box[0]) <= 0.6 * width]
    spans = sorted((b.blocks[w].box[0], b.blocks[w].box[2]) for w in narrow)
    columns: list[list[float]] = []
    for lo, hi in spans:
        if columns and lo <= columns[-1][1] + 2:
            columns[-1][1] = max(columns[-1][1], hi)
        else:
            columns.append([lo, hi])
    col_bands = [(c[0], c[1]) for c in columns] or [(region.box[0], region.box[2])]
    row_numbers = sorted({int(b.blocks[w].attrs["row"]) for w in words})
    row_index = {r: i for i, r in enumerate(row_numbers)}
    place = {
        w: (
            row_index[int(b.blocks[w].attrs["row"])],
            _nearest(_center(b.blocks[w].box)[0], col_bands),
        )
        for w in words
    }
    return place, len(row_numbers), len(col_bands)


# ---- Label → value ----


@dataclass
class _Pair:
    key: tuple[str, ...]
    value: tuple[str, ...]
    rule: str
    confidence: float
    key_span: tuple[int, int] | None = None
    value_span: tuple[int, int] | None = None
    """"label: value" within one block: the character ranges of the label and of the value."""


def _key_values(b: _Builder, in_table: set[str], tables: list[str]) -> list[str]:
    pairs: list[_Pair] = []
    used_key: set[str] = set()
    used_value: set[str] = set()

    def take(pair: _Pair) -> None:
        if any(w in used_value for w in pair.value) or any(w in used_key for w in pair.key):
            return
        if pair.key_span is None:  # Label and value within one block are not marked as used.
            used_key.update(pair.key)
            used_value.update(pair.value)
        else:
            used_key.update(pair.key)
        pairs.append(pair)

    free = [w for w in b.words if w not in in_table]
    by_row: dict[int, list[str]] = {}
    for w in free:
        by_row.setdefault(int(b.blocks[w].attrs["row"]), []).append(w)

    # 1. "label: value" within one block
    for w in free:
        m = _INLINE.match(b.blocks[w].text.strip())
        if m and _label_like(m["key"]):
            text = b.blocks[w].text
            start = text.find(m["value"])
            take(
                _Pair(
                    (w,),
                    (w,),
                    "inline",
                    0.9,
                    key_span=(0, text.find(m["key"]) + len(m["key"]) + 1),
                    value_span=(start, start + len(m["value"])),
                )
            )

    # 2. Colon-terminated label: value to the right or directly below
    for w in free:
        text = b.blocks[w].text.strip()
        if not text.endswith((":", "：")) or not _label_like(text[:-1]) or w in used_key:
            continue
        row = int(b.blocks[w].attrs["row"])
        right = [
            x
            for x in by_row.get(row, [])
            if b.blocks[x].box[0] > b.blocks[w].box[2]
            and b.blocks[x].box[0] - b.blocks[w].box[2] <= WIDE_GAP * b.page.width
        ]
        if right:
            value = [right[0], *_continued_below(b, right[0], free)]
            take(_Pair((w,), tuple(value), "colon", 0.85))
            continue
        below = _just_below(b, w, free)
        if below is not None:
            value = [below, *_continued_below(b, below, free)]
            take(_Pair((w,), tuple(value), "colon_below", 0.75))

    # 3. A line of labels with values in columns below
    for above, below in zip(b.rows, b.rows[1:], strict=False):
        heads = [w for w in b.word_ids(above.lines) if w in by_row.get(above.index, [])]
        if len(heads) < 2 or not all(_label_like(b.blocks[h].text) for h in heads):
            continue
        h = _row_height(above, below)
        gap = min(ln.bbox[1] for ln in below.lines) - max(ln.bbox[3] for ln in above.lines)
        if not -0.3 * h <= gap <= 1.5 * h:
            continue
        cells = [w for w in b.word_ids(below.lines) if w in by_row.get(below.index, [])]
        columns = {w: _column_of(b, w, heads, h) for w in cells}
        hit = [c for c in columns.values() if c is not None]
        if len(set(hit)) < 2 or len(hit) < 0.6 * max(len(cells), 1):
            continue
        values: dict[str, list[str]] = {}
        for w, head in columns.items():
            if head is not None:
                values.setdefault(head, []).append(w)
        # Lines below that continue the previous line are appended to the column they align with.
        previous = below
        for nxt in b.rows[below.index :]:
            if not continues(previous, nxt):
                break
            for w in b.word_ids(nxt.lines):
                head = _column_of(b, w, heads, h)
                if head is not None and head in values and w in free:
                    values[head].append(w)
            previous = nxt
        for head in heads:
            if head in values:
                take(_Pair((head,), tuple(values[head]), "header_above", 0.8))

    # 4. Two-column tables whose left column holds labels
    for t in tables:
        table = b.blocks[t]
        if table.attrs["cols"] != 2:
            continue
        rows = [
            (b.blocks[f"c{t[1:]}-{r}-1"], b.blocks[f"c{t[1:]}-{r}-2"])
            for r in range(1, table.attrs["rows"] + 1)
        ]
        labels = [k for k, _ in rows if k.children and _label_like(k.text)]
        if len(labels) < 0.6 * len(rows):
            continue
        _, head_v = rows[0]
        if head_v.children and _label_like(head_v.text):
            # Both cells of the first row are headers ("DESCRIPTION  AMOUNT"): an ordinary table,
            # not label → value.
            continue
        for k, v in rows:
            if k.children and v.children and _label_like(k.text):
                take(_Pair(k.children, v.children, "two_column_table", 0.8))

    # 5. Exactly two blocks on a line: label-like on the left, a number on the right
    for row_words in by_row.values():
        if len(row_words) == 2:
            left, right = row_words
            if _label_like(b.blocks[left].text) and _DIGIT.search(b.blocks[right].text):
                take(_Pair((left,), (right,), "label_left", 0.6))

    # 6. Table rows not recognized as a table: the row name on the far left, at least two more
    # blocks to the right on the same horizontal line, and an amount at the far right ("Room Charges
    # Rs 1939 x 1 Night x 1 Room   Rs 1939"). The same line is judged by position rather than line
    # number, since the row name and the amount are sometimes assigned to different layout regions
    # with different line numbers.
    for w in free:
        text = b.blocks[w].text.strip()
        if (
            w in used_key
            or text.endswith((":", "："))
            or _DIGIT.search(text)
            or not _label_like(text)
        ):
            continue
        _, y0, x1, y1 = b.blocks[w].box
        middle, height = (y0 + y1) / 2, max(1.0, y1 - y0)
        same_line = sorted(
            (
                v
                for v in free
                if v != w
                and b.blocks[v].box[0] > x1
                and abs((b.blocks[v].box[1] + b.blocks[v].box[3]) / 2 - middle) <= 0.4 * height
            ),
            key=lambda v: b.blocks[v].box[0],
        )
        if len(same_line) >= 2 and _AMOUNT.fullmatch(b.blocks[same_line[-1]].text.strip()):
            take(_Pair((w,), (same_line[-1],), "row_label", 0.5))

    keys: list[str] = []
    for i, pair in enumerate(pairs, start=1):
        kid, vid = f"k{i}", f"v{i}"
        key_text = _part(b, pair.key, pair.key_span).rstrip(" :：")
        value_text = _part(b, pair.value, pair.value_span)
        b.add(
            Block(
                vid,
                "VALUE",
                value_text,
                b.union(pair.value),
                min(pair.confidence, _min_conf(b, pair.value)),
                pair.value,
                {"key": kid, "span": list(pair.value_span) if pair.value_span else None},
            )
        )
        b.add(
            Block(
                kid,
                "KEY",
                key_text,
                b.union(pair.key),
                min(pair.confidence, _min_conf(b, pair.key)),
                pair.key,
                {
                    "value": vid,
                    "rule": pair.rule,
                    "span": list(pair.key_span) if pair.key_span else None,
                },
            )
        )
        keys.append(kid)
    return keys


_AMOUNT = re.compile(
    r"(?:[A-Za-z]{2,3}\.?\s+)?[-+(]?\s*[$€£¥₹]?\s*\d+(?:[,\s]\d{3})*(?:\.\d+)?\)?\s*[A-Za-z%]{0,4}"
)
"""The whole block is an amount or number (Rs 1939, $4.11, 9.00, 10 kg): the rightmost value of a
table row."""


def _part(b: _Builder, ids: tuple[str, ...], span: tuple[int, int] | None) -> str:
    text = b.text(ids)
    return text[span[0] : span[1]].strip(" :：") if span else text


def _row_height(*rows: OcrRow) -> float:
    return min(median(ln.bbox[3] - ln.bbox[1] for ln in r.lines) for r in rows) or 1.0


def _column_of(b: _Builder, word: str, heads: list[str], h: float) -> str | None:
    """Header under which this value block sits: left-aligned (within 1.5 text heights), or its
    center within the header's horizontal extent."""
    x0, _, x1, _ = b.blocks[word].box
    cx = (x0 + x1) / 2
    best: tuple[float, str] | None = None
    for head in heads:
        hx0, _, hx1, _ = b.blocks[head].box
        if abs(x0 - hx0) <= 1.5 * h or hx0 <= cx <= hx1:
            d = abs(x0 - hx0)
            if best is None or d < best[0]:
                best = (d, head)
    return None if best is None else best[1]


def _has_own_label(b: _Builder, word: str, free: list[str]) -> bool:
    """This block has its own label to its left on the same line ("Cashier :  MANIS"): it is another
    value, not a continuation of the previous one."""
    row = b.blocks[word].attrs["row"]
    x0 = b.blocks[word].box[0]
    for w in free:
        block = b.blocks[w]
        if w == word or block.attrs["row"] != row or block.box[2] > x0:
            continue
        text = block.text.strip()
        if text.endswith((":", "：")) or _label_like(text):
            return True
    return False


def _just_below(b: _Builder, word: str, free: list[str]) -> str | None:
    """The nearby left-aligned block directly below."""
    x0, _, _, y1 = b.blocks[word].box
    h = b.blocks[word].box[3] - b.blocks[word].box[1]
    found = [
        w
        for w in free
        if 0 <= b.blocks[w].box[1] - y1 <= 1.5 * h and abs(b.blocks[w].box[0] - x0) <= 1.5 * h
    ]
    return min(found, key=lambda w: b.blocks[w].box[1]) if found else None


def _continued_below(b: _Builder, word: str, free: list[str]) -> list[str]:
    """Wrapped value: nearby left-aligned blocks of the same text size directly below (at most 5
    more)."""
    found: list[str] = []
    current = word
    for _ in range(5):
        x0, _, _, y1 = b.blocks[current].box
        h = b.blocks[current].box[3] - b.blocks[current].box[1]
        nxt = [
            w
            for w in free
            if w not in found
            and w != word
            and 0 <= b.blocks[w].box[1] - y1 <= 0.8 * h
            and abs(b.blocks[w].box[0] - x0) <= 1.5 * h
            and _WORDY.search(b.blocks[w].text)
            and not _INLINE.match(b.blocks[w].text.strip())
            and not b.blocks[w].text.strip().endswith((":", "："))
            and not _has_own_label(b, w, free)
        ]
        if not nxt:
            break
        current = min(nxt, key=lambda w: b.blocks[w].box[1])
        found.append(current)
    return found
