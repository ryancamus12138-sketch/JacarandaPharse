"""把页面结构写成给大模型看的文字（ADR 0034）。

两部分：
1. 版面原样（LAPDoc 的 SpatialFormat）：按每块文字在页面上的横向位置用空格排开，
   上下空得多的地方空一行。模型不用算坐标，一眼就能看出谁在谁下面、哪几块是一列。
   每块前面带编号 ⟨n⟩（LMDX 的做法）：模型回答值的时候说"用了哪几块"，系统再去核对。
2. 结构：版面区域、"标题 → 值"、表格（每格是哪几块），都用编号写。
"""

from __future__ import annotations

from jacara.structure.graph import Block, PageStructure

GRID = 120
"""页面宽度换算成多少个字符的宽度。"""


def mark(n: int) -> str:
    return f"⟨{n}⟩"


def spatial_text(s: PageStructure) -> str:
    """版面原样：一行一个视觉行，每块按横向位置排，块前面带 ⟨编号⟩。"""
    out: list[str] = []
    previous: Block | None = None
    for line_id in s.lines:
        line = s.blocks[line_id]
        if previous is not None:
            h = max(min(line.box[3] - line.box[1], previous.box[3] - previous.box[1]), 1.0)
            if line.box[1] - previous.box[3] > 1.2 * h:
                out.append("")  # 上下空得多：空一行
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
    """版面区域、标题 → 值、表格。都没有就是空字符串。"""
    parts: list[str] = []
    # 正文是默认的，不用说；只列出页眉、页脚、标题、表格这些有用的区域
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
