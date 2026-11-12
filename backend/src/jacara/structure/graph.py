"""页面结构的数据：文字块和它们之间的关系（照 AWS Textract 的 Block 结构）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from jacara.plugins.ocr.base import OcrLine, OcrPage

BlockType = Literal[
    "PAGE", "LINE", "WORD", "LAYOUT", "KEY", "VALUE", "TABLE", "CELL", "MERGED_CELL"
]

Box = tuple[float, float, float, float]
"""(x0, y0, x1, y1)，单位和 OCR 的坐标一样（页面图片上的像素）。"""


@dataclass(frozen=True)
class Block:
    """结构里的一个块。

    id：WORD 是 "w12"（12 就是给模型看的编号），其他是 "l3"、"k1"、"v1"、"t1"、"c1-2-3"……
    children：CHILD 关系，指向组成它的块（一般是 WORD）。
    attrs：各类块自己的信息：LAYOUT 的 kind；CELL 的 row、col、row_span、col_span；
           KEY 的 value（指向 VALUE 块）、rule（哪条规则找出来的）；WORD 的 row（第几个视觉行）。
    """

    id: str
    type: BlockType
    text: str
    box: Box
    confidence: float
    children: tuple[str, ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class PageStructure:
    """一页的结构。words 按阅读顺序（先上后下、同一行先左后右），第 n 个就是编号 n。"""

    width: float
    height: float
    blocks: dict[str, Block]
    words: tuple[str, ...]
    lines: tuple[str, ...]
    layouts: tuple[str, ...]
    tables: tuple[str, ...]
    keys: tuple[str, ...]
    """KEY 块（每个都有 attrs["value"] 指向它的 VALUE 块）。"""
    ocr_lines: tuple[OcrLine, ...]
    """和 words 一一对应的 OCR 文字块（带四个角的坐标），出处框用。"""
    sources: dict[str, str] = field(default_factory=dict, compare=False, hash=False)
    """结构是怎么来的：{"layout": "pp_layout_cdla", "table": "slanet-plus.onnx" 或 "规则"}。"""

    def word(self, number: int) -> Block | None:
        """编号 → 文字块（编号从 1 开始）。"""
        return self.blocks.get(f"w{number}")

    def number_of(self, block_id: str) -> int:
        return int(block_id[1:])

    def ocr_line(self, number: int) -> OcrLine | None:
        return self.ocr_lines[number - 1] if 1 <= number <= len(self.ocr_lines) else None

    def words_of(self, block_id: str) -> list[Block]:
        """一个块（KEY、VALUE、CELL……）由哪些文字块组成，按阅读顺序。"""
        found: dict[str, Block] = {}
        stack = [block_id]
        while stack:  # MERGED_CELL → CELL → WORD：一层层往下找
            for child in self.blocks[stack.pop()].children:
                if child.startswith("w"):
                    found[child] = self.blocks[child]
                elif child in self.blocks:
                    stack.append(child)
        return sorted(found.values(), key=lambda b: self.number_of(b.id))

    def numbers(self, block_id: str) -> list[int]:
        return [self.number_of(w.id) for w in self.words_of(block_id)]

    def to_json(self) -> dict[str, Any]:
        """存进数据库（extractions.structure）的格式：只存块和关系，坐标取一位小数。"""
        return {
            "width": self.width,
            "height": self.height,
            "sources": self.sources,
            "words": list(self.words),
            "lines": list(self.lines),
            "layouts": list(self.layouts),
            "tables": list(self.tables),
            "keys": list(self.keys),
            "blocks": [
                {
                    "id": b.id,
                    "type": b.type,
                    "text": b.text,
                    "box": [round(v, 1) for v in b.box],
                    "confidence": round(b.confidence, 4),
                    "children": list(b.children),
                    "attrs": b.attrs,
                }
                for b in self.blocks.values()
            ],
        }


def structure_from_json(data: dict[str, Any], page: OcrPage) -> PageStructure | None:
    """存下来的结构 + 这一页的 OCR（page_from_json 还原的）→ PageStructure。试跑时用。

    文字块和 OCR 文字块靠 attrs["ocr"]（在 OCR 结果里是第几块）对上；对不上（数据不完整）返回 None，
    调用方重新按规则分析。
    """
    try:
        blocks = {
            b["id"]: Block(
                b["id"],
                b["type"],
                b["text"],
                cast(Box, tuple(b["box"])),
                float(b["confidence"]),
                tuple(b.get("children", [])),
                dict(b.get("attrs") or {}),
            )
            for b in data["blocks"]
        }
        words = tuple(data["words"])
        lines = tuple(page.lines[int(blocks[w].attrs["ocr"])] for w in words)
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return PageStructure(
        width=float(data.get("width") or page.width),
        height=float(data.get("height") or page.height),
        blocks=blocks,
        words=words,
        lines=tuple(data.get("lines", [])),
        layouts=tuple(data.get("layouts", [])),
        tables=tuple(data.get("tables", [])),
        keys=tuple(data.get("keys", [])),
        ocr_lines=lines,
        sources=dict(data.get("sources") or {}),
    )
