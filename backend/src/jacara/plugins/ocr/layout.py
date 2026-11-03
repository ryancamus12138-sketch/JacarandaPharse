"""把 OCR 的"文字块"按图上的位置合并成"视觉行"。

OCR 给的是一块一块的文字。收据上"Date:"和"25/12/2018"是同一行左右两块，
但 OCR 会把它们当成两条。按 y 坐标把它们合回一行，大模型才看得出谁和谁是一对。
抽取器和证据对齐都用这里的行号，所以两边说的"第几行"永远是同一行。
"""

from __future__ import annotations

from dataclasses import dataclass

from jacara.plugins.ocr.base import OcrLine, OcrPage


@dataclass(frozen=True)
class OcrRow:
    """图上的一行：由一个或多个文字块组成，从左到右排列。index 从 1 开始。"""

    index: int
    lines: tuple[OcrLine, ...]

    @property
    def text(self) -> str:
        """这一行的全部文字，文字块之间用两个空格隔开。"""
        return "  ".join(line.text for line in self.lines)


def group_rows(page: OcrPage, overlap: float = 0.5) -> tuple[OcrRow, ...]:
    """按 y 坐标把文字块合并成行。

    两个文字块上下重叠的高度超过较矮那个的一半（overlap），就算同一行。
    """
    ordered = sorted(page.lines, key=lambda ln: (ln.bbox[1] + ln.bbox[3]) / 2)
    groups: list[list[OcrLine]] = []
    for line in ordered:
        _, y0, _, y1 = line.bbox
        if groups:
            row = groups[-1]
            row_y0 = min(ln.bbox[1] for ln in row)
            row_y1 = max(ln.bbox[3] for ln in row)
            shared = min(y1, row_y1) - max(y0, row_y0)
            if shared > overlap * min(y1 - y0, row_y1 - row_y0):
                row.append(line)
                continue
        groups.append([line])

    return tuple(
        OcrRow(index=i, lines=tuple(sorted(group, key=lambda ln: ln.bbox[0])))
        for i, group in enumerate(groups, start=1)
    )


def numbered_text(rows: tuple[OcrRow, ...]) -> str:
    """给大模型看的文字：每行前面加上 [行号]。"""
    return "\n".join(f"[{row.index}] {row.text}" for row in rows)
