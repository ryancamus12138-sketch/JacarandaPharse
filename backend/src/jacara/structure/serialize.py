"""Serialize page structure as text for the LLM.

Two parts:
1. Layout-preserving text (LAPDoc SpatialFormat): each block is placed with spaces according to
   its horizontal position on the page, with an empty line where vertical gaps are large. The
   model sees at a glance what lies below what and which blocks form a column, without computing
   coordinates. Each block is prefixed with an index ⟨n⟩ (the LMDX approach): the model names the
   blocks used for each value, and the system verifies them.
2. Structure: layout regions, label → value pairs and tables (blocks per cell), all by index."""

from __future__ import annotations

from jacara.structure.graph import Block, PageStructure

GRID = 120
"""Page width expressed in characters."""


def mark(n: int) -> str:
    return f"⟨{n}⟩"


def spatial_text(s: PageStructure) -> str:
    """Layout-preserving text: one visual line per line, blocks placed by horizontal position and
    prefixed with ⟨index⟩."""
    out: list[str] = []
    previous: Block | None = None
    page = 0
    for line_id in s.lines:
        line = s.blocks[line_id]
        # Stacked documents: write a separator line at each new page.
        while page + 1 < len(s.page_starts) and line.box[1] >= s.page_starts[page + 1]:
            page += 1
            out += ["", f"──────── 第 {page + 1} 页 ────────", ""]
            previous = None
        if previous is not None:
            h = max(min(line.box[3] - line.box[1], previous.box[3] - previous.box[1]), 1.0)
            if line.box[1] - previous.box[3] > 1.2 * h:
                out.append("")  # Large vertical gap: add an empty line.
        text = ""
        for word in sorted((s.blocks[c] for c in line.children), key=lambda w: w.box[0]):
            column = round(word.box[0] / max(s.width, 1.0) * GRID)
            token = f"{mark(s.number_of(word.id))}{word.text}"
            if not text:
                text = " " * column + token
            elif column > len(text) + 1:
                text += " " * (column - len(text)) + token
            else:
                text += "  " + token
        out.append(text.rstrip())
        previous = line
    return "\n".join(out)


def _marks(s: PageStructure, block_id: str) -> str:
    return "".join(mark(n) for n in s.numbers(block_id))


def structure_text(s: PageStructure) -> str:
    """Layout regions, label → value pairs and tables. Empty string if there are none."""
    parts: list[str] = []
    # Text is the default and needs no mention; list only useful regions such as headers, footers,
    # titles and tables.
    regions = [
        s.blocks[a]
        for a in s.layouts
        if s.blocks[a].children and s.blocks[a].attrs.get("kind") != "正文"
    ]
    if regions:
        items = []
        for r in regions:
            numbers = s.numbers(r.id)
            where = (
                mark(numbers[0])
                if len(numbers) == 1
                else f"{mark(numbers[0])}～{mark(numbers[-1])}"
            )
            items.append(f"{r.attrs.get('kind', '')} {where}")
        parts.append("版面区域：" + "；".join(items))
    if s.keys:
        lines = []
        for k in s.keys:
            key = s.blocks[k]
            value = s.blocks[key.attrs["value"]]
            same = key.children == value.children
            where = "（同一块）" if same else ""
            lines.append(
                f"- {_marks(s, k)}{key.text} → {_marks(s, value.id) if not same else ''}"
                f"{value.text}{where}"
            )
        parts.append("标题 → 值（按位置找出来的，供参考）：\n" + "\n".join(lines))
    for i, t in enumerate(s.tables, start=1):
        table = s.blocks[t]
        rows, cols = table.attrs["rows"], table.attrs["cols"]
        merged = {
            cell: s.blocks[m]
            for m in table.attrs.get("merged", [])
            for cell in s.blocks[m].children
        }
        lines = []
        for r in range(1, rows + 1):
            cells = []
            for c in range(1, cols + 1):
                cid = f"c{t[1:]}-{r}-{c}"
                m = merged.get(cid)
                if m is not None:
                    first = m.children[0] == cid
                    span = f"（跨 {m.attrs['row_span']} 行 {m.attrs['col_span']} 列）"
                    cells.append(f"{_marks(s, m.id)}{m.text}{span}" if first else "〃")
                else:
                    cell = s.blocks[cid]
                    cells.append(f"{_marks(s, cid)}{cell.text}" if cell.children else "（空）")
            lines.append(f"第 {r} 行 | " + " | ".join(cells) + " |")
        parts.append(f"表格 {i}（{rows} 行 × {cols} 列）：\n" + "\n".join(lines))
    return "\n\n".join(parts)
