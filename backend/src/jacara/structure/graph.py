"""Page structure data: text blocks and their relationships (modeled on the AWS Textract Block
structure)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from jacara.plugins.ocr.base import OcrLine, OcrPage

BlockType = Literal[
    "PAGE", "LINE", "WORD", "LAYOUT", "KEY", "VALUE", "TABLE", "CELL", "MERGED_CELL"
]

Box = tuple[float, float, float, float]
"""(x0, y0, x1, y1), in OCR coordinates (pixels of the page image)."""


@dataclass(frozen=True)
class Block:
    """A block of the structure.

        id: "w12" for WORD (12 is the index shown to the model); "l3", "k1", "v1", "t1",
    "c1-2-3", … for others.
        children: CHILD relationships to the blocks it consists of (usually WORD).
        attrs: block-type specific data: kind for LAYOUT; row, col, row_span, col_span for CELL;
    value (the VALUE block) and rule (the rule that found it) for KEY; row (visual line number)
    for WORD."""

    id: str
    type: BlockType
    text: str
    box: Box
    confidence: float
    children: tuple[str, ...] = ()
    attrs: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class PageStructure:
    """Structure of one page. words are in reading order (top to bottom, left to right within a
    line); the n-th has index n."""

    width: float
    height: float
    blocks: dict[str, Block]
    words: tuple[str, ...]
    lines: tuple[str, ...]
    layouts: tuple[str, ...]
    tables: tuple[str, ...]
    keys: tuple[str, ...]
    """KEY blocks (each with attrs["value"] pointing to its VALUE block)."""
    ocr_lines: tuple[OcrLine, ...]
    """OCR text blocks (with four-corner coordinates) corresponding one-to-one to words; used for
    evidence boxes."""
    sources: dict[str, str] = field(default_factory=dict, compare=False, hash=False)
    """Provenance of the structure: {"layout": "pp_layout_cdla", "table": "slanet-plus.onnx" or
    "rules"}."""
    page_starts: tuple[float, ...] = ()
    """For stacked documents, the y offset at which each page begins. Empty for a single page."""

    def word(self, number: int) -> Block | None:
        """Index → text block (1-based)."""
        return self.blocks.get(f"w{number}")

    def number_of(self, block_id: str) -> int:
        return int(block_id[1:])

    def ocr_line(self, number: int) -> OcrLine | None:
        return self.ocr_lines[number - 1] if 1 <= number <= len(self.ocr_lines) else None

    def words_of(self, block_id: str) -> list[Block]:
        """Text blocks forming a block (KEY, VALUE, CELL, …), in reading order."""
        found: dict[str, Block] = {}
        stack = [block_id]
        while stack:  # MERGED_CELL → CELL → WORD: descend level by level.
            for child in self.blocks[stack.pop()].children:
                if child.startswith("w"):
                    found[child] = self.blocks[child]
                elif child in self.blocks:
                    stack.append(child)
        return sorted(found.values(), key=lambda b: self.number_of(b.id))

    def numbers(self, block_id: str) -> list[int]:
        return [self.number_of(w.id) for w in self.words_of(block_id)]

    def to_json(self) -> dict[str, Any]:
        """Format stored in the database (extractions.structure): blocks and relationships only,
        coordinates rounded to one decimal."""
        return {
            "width": self.width,
            "height": self.height,
            "sources": self.sources,
            **({"page_starts": list(self.page_starts)} if self.page_starts else {}),
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
    """Stored structure + this page's OCR (restored by page_from_json) → PageStructure. Used by
    trial runs.

        Text blocks are matched to OCR blocks through attrs["ocr"] (index in the OCR result); on
    mismatch (incomplete data) returns None and the caller re-analyzes by rules."""
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
        page_starts=tuple(float(v) for v in data.get("page_starts") or ()),
    )
