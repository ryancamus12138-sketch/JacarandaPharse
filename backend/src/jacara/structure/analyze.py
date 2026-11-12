"""一页 OCR 结果 → 页面结构（ADR 0034）：文字块、行、版面区域、表格、"标题 → 值"。

步骤（照 AWS Textract 的 LAYOUT、TABLES、FORMS 三部分）：
1. WORD、LINE：OCR 的文字块按阅读顺序编号；按位置合成视觉行（plugins/ocr/layout.py）。
2. LAYOUT：有版面模型就找出区域（标题、正文、表格、页眉页脚……），区域里的文字块归给它。
3. TABLE / CELL / MERGED_CELL：版面模型找出的每张表，有表格结构模型就认出行列和合并的格子；
   没有表格模型，按文字位置划分（每个视觉行一行，文字在横向上的投影分成几段就是几列）。
4. KEY / VALUE：Textract 用训练好的模型找"标题 → 值"。能离线、能商用的同类模型（LayoutXLM 系列）
   许可证不允许商用，所以这里按版面规则找，五种情况：
   - 同一块里"标题：值"（"Date: 31/12/2017"）
   - 冒号结尾的标题，值在右边或正下方（"Guest Name:" "Sanjay"）
   - 上面一行是几个标题，下面按列写值（"Check In  Check Out  Rooms" / "31/12/2017  01/01/2018  1"）
   - 两列的表格，左边一列是标题
   - 同一行只有两块：左边像标题、右边是数字（"Grand Total   Rs 1939"）
   找到的只是结构，最后哪个值填哪个字段由大模型决定，再由证据对齐核对。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

import numpy as np

from jacara.plugins.ocr.base import OcrLine, OcrPage
from jacara.plugins.ocr.layout import OcrRow, continues, group_rows
from jacara.structure.engines import NO_MODELS, TABLE, CellGuess, Region, StructureEngines
from jacara.structure.graph import Block, Box, PageStructure

_WORDY = re.compile(r"[A-Za-z一-鿿]")
_DIGIT = re.compile(r"\d")
_INLINE = re.compile(r"^(?P<key>[^:：\d]{1,40}?)\s*[:：]\s*(?P<value>\S.*)$")
"""同一块里的"标题：值"。标题里不能有数字（"8:13" 不是标题和值）。"""

WIDE_GAP = 0.35
"""冒号结尾的标题和右边的值，横向空隙不超过页宽的 35%。"""


def _box(line: OcrLine) -> Box:
    return line.bbox


def _center(box: Box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _inside(box: Box, area: Box, pad: float = 2.0) -> bool:
    cx, cy = _center(box)
    return area[0] - pad <= cx <= area[2] + pad and area[1] - pad <= cy <= area[3] + pad


def _label_like(text: str) -> bool:
    """像标题：有字、没有数字、不长。"""
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
    """id(OcrLine) → WORD 的 id。"""

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
    page: OcrPage, image: np.ndarray | None = None, engines: StructureEngines = NO_MODELS
) -> PageStructure:
    """分析一页的结构。image 是这一页的图（坐标和 OCR 一样）；没有图就不用模型，只按规则。"""
    rows = group_rows(page)
    b = _Builder(page, rows, {}, [], [], [], {})
    index_of = {id(ln): i for i, ln in enumerate(page.lines)}

    # 1. WORD、LINE
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
    # 版面模型有时把同一块地方报两次（一次"标题"、一次"正文"）：同样几块文字只留分数高的那个
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
            sources.setdefault("table", "规则")
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
    )


def _min_conf(b: _Builder, ids: Iterable[str]) -> float:
    return min((b.blocks[i].confidence for i in ids), default=1.0)


# ---- 表格 ----


def _recognize(image: np.ndarray, area: Box, engines: StructureEngines) -> list[CellGuess]:
    """把表格那一块裁出来交给表格模型，格子的框换回整页的坐标。"""
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
    """每一行（或列）占的范围：这一行里所有不跨行的格子的上下边界合起来。没有的按两边插。"""
    found: dict[int, tuple[float, float]] = {}
    for index, lo, hi in spans:
        old = found.get(index)
        found[index] = (lo, hi) if old is None else (min(old[0], lo), max(old[1], hi))
    return [found.get(i, (float("nan"), float("nan"))) for i in range(count)]


def _nearest(value: float, bands: list[tuple[float, float]]) -> int:
    best, best_d = 0, float("inf")
    for i, (lo, hi) in enumerate(bands):
        if lo != lo:  # nan：这一行没有自己的格子
            continue
        d = 0.0 if lo <= value <= hi else min(abs(value - lo), abs(value - hi))
        if d < best_d:
            best, best_d = i, d
    return best


def _build_table(b: _Builder, number: int, region: Block, guesses: list[CellGuess]) -> Block | None:
    """一张表：先定出有几行几列、每个文字块在哪一格，再生成 CELL（每个位置一个）和 MERGED_CELL。"""
    words = list(region.children)
    spans: list[tuple[int, int, int, int]] = []  # 合并的格子：(行, 行尾, 列, 列尾)
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
    """没有表格模型时：每个视觉行一行；把文字块在横向上的范围投影到一起，连起来的算一列。

    很宽的块（超过表格宽度六成，例如表格里的一行说明）不参与分列，免得把几列连成一列。
    """
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


# ---- 标题 → 值 ----


@dataclass
class _Pair:
    key: tuple[str, ...]
    value: tuple[str, ...]
    rule: str
    confidence: float
    key_span: tuple[int, int] | None = None
    value_span: tuple[int, int] | None = None
    """同一块里的"标题：值"：标题、值各占这块文字的哪一段（第几个字到第几个字）。"""


def _key_values(b: _Builder, in_table: set[str], tables: list[str]) -> list[str]:
    pairs: list[_Pair] = []
    used_key: set[str] = set()
    used_value: set[str] = set()

    def take(pair: _Pair) -> None:
        if any(w in used_value for w in pair.value) or any(w in used_key for w in pair.key):
            return
        if pair.key_span is None:  # 同一块的标题和值不算"已经用掉"
            used_key.update(pair.key)
            used_value.update(pair.value)
        else:
            used_key.update(pair.key)
        pairs.append(pair)

    free = [w for w in b.words if w not in in_table]
    by_row: dict[int, list[str]] = {}
    for w in free:
        by_row.setdefault(int(b.blocks[w].attrs["row"]), []).append(w)

    # ① 同一块里"标题：值"
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

    # ② 冒号结尾的标题：值在右边，或者在正下方
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

    # ③ 上面一行是几个标题，下面按列写值
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
        # 下面"接着上一行写"的行，对得上哪一栏就接到哪一栏
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

    # ④ 两列的表格，左边一列是标题
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
            continue  # 第一行两格都是标题（"DESCRIPTION  AMOUNT"）：这是普通的表，不是"标题 → 值"
        for k, v in rows:
            if k.children and v.children and _label_like(k.text):
                take(_Pair(k.children, v.children, "two_column_table", 0.8))

    # ⑤ 同一行只有两块：左边像标题，右边有数字
    for row_words in by_row.values():
        if len(row_words) == 2:
            left, right = row_words
            if _label_like(b.blocks[left].text) and _DIGIT.search(b.blocks[right].text):
                take(_Pair((left,), (right,), "label_left", 0.6))

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


def _part(b: _Builder, ids: tuple[str, ...], span: tuple[int, int] | None) -> str:
    text = b.text(ids)
    return text[span[0] : span[1]].strip(" :：") if span else text


def _row_height(*rows: OcrRow) -> float:
    return min(median(ln.bbox[3] - ln.bbox[1] for ln in r.lines) for r in rows) or 1.0


def _column_of(b: _Builder, word: str, heads: list[str], h: float) -> str | None:
    """这块值在哪个标题下面：左边对齐（差不到 1.5 个字高），或者中心落在标题的左右范围里。"""
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


def _just_below(b: _Builder, word: str, free: list[str]) -> str | None:
    """正下方左对齐、挨得近的那一块。"""
    x0, _, _, y1 = b.blocks[word].box
    h = b.blocks[word].box[3] - b.blocks[word].box[1]
    found = [
        w
        for w in free
        if 0 <= b.blocks[w].box[1] - y1 <= 1.5 * h and abs(b.blocks[w].box[0] - x0) <= 1.5 * h
    ]
    return min(found, key=lambda w: b.blocks[w].box[1]) if found else None


def _continued_below(b: _Builder, word: str, free: list[str]) -> list[str]:
    """值折行：正下方左对齐、挨得近、字一样大的几块（最多再接 5 块）。"""
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
        ]
        if not nxt:
            break
        current = min(nxt, key=lambda w: b.blocks[w].box[1])
        found.append(current)
    return found
