"""证据对齐扩展点：给每个抽出来的值，在原文里找到出处。

大模型说"总额是 9.0、在第 17 行"，这只是它的说法。证据对齐回到 OCR 结果里核对：
值确实在那里，就记下位置和 OCR 置信度；对不上，就标出来交给人工确认。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from jacara.plugins.extractors.base import ExtractedField, ExtractionResult
from jacara.plugins.ocr.base import OcrLine, OcrPage

AlignStatus = Literal["matched", "fuzzy", "not_found", "empty", "rejected", "computed", "seen"]
"""matched：原文里找到了一样的值。fuzzy：只找到相似的值。
not_found：原文里找不到，可能是模型编的。empty：模型说原文里没有这个字段。
rejected：模型给了值，但它说用到的文字块里没有、原文别处也找不到，按 LMDX 的做法丢掉（ADR 0034）；
存的值是空的，模型给的值另外记下，人核对后可以一键采用。
computed：不是从原文找的，是按模板的公式由别的字段算出来的（ADR 0035），没有出处框。
seen：模型看着原图读出来的（ADR 0038），OCR 认错了或漏了，文字里对不上。值留着、标黄要人核对；
模型说了是哪几块（OCR 认错的）就框那几块，没说就没有出处框。"""


@dataclass(frozen=True)
class Evidence:
    """一个值的出处：在第几行、是哪几个文字块（带坐标）、相似度多高。"""

    row: int
    row_text: str
    lines: tuple[OcrLine, ...]
    similarity: float

    @property
    def confidence(self) -> float:
        """出处那几个文字块里最低的 OCR 置信度：最不确定的那块决定了能不能放心。"""
        return min(line.confidence for line in self.lines)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """把出处的文字块合成一个框 (x0, y0, x1, y1)，审核页面用它在原图上高亮。"""
        boxes = [line.bbox for line in self.lines]
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


@dataclass(frozen=True)
class AlignedField:
    """对齐之后的字段：原来的抽取结果 + 核对状态 + 出处 + 是否需要人工确认 + 原因。"""

    field: ExtractedField
    status: AlignStatus
    evidence: Evidence | None
    needs_review: bool
    note: str = ""
    """给人看的说明，例如"原文里找不到"。审核页面直接显示。"""


class Aligner(Protocol):
    """证据对齐接口：任何类只要有 name 属性和 align 方法，就是一个对齐器。"""

    name: str

    def align(self, page: OcrPage, result: ExtractionResult) -> tuple[AlignedField, ...]:
        """逐个字段在 OCR 结果里找出处，顺序和 result.fields 一致。"""
        ...
