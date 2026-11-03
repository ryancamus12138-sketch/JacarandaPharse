"""字段抽取扩展点：规定"抽什么字段"和"抽出来的结果长什么样"。

抽取器拿到 OCR 结果，按字段清单填值。后面的证据对齐、规则校验只认这里的类型，
不关心值是大模型抽的、规则抽的，还是以后别的方法。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from jacara.plugins.ocr.base import OcrPage

FieldType = Literal["string", "number", "date"]


class ExtractionError(RuntimeError):
    """模型的回答不是合格的 JSON，或缺了字段。"""


@dataclass(frozen=True)
class FieldSpec:
    """要抽取的一个字段：名字、类型、给模型看的说明。"""

    name: str
    type: FieldType
    description: str


@dataclass(frozen=True)
class ExtractedField:
    """抽出来的一个字段。value 为 None 表示原文里找不到。

    row 是模型说的"值在第几行"，只是线索，要由证据对齐去核对。
    """

    name: str
    type: FieldType
    value: str | float | None
    row: int | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """一次抽取的结果，以及"它是怎么来的"。

    raw_output 是模型的原始回答，永远保留（项目规则：原始值不覆盖）。
    model、backend、prompt_version 用来追溯：同样的输入，换了模型或提示词结果可能不同。
    """

    fields: tuple[ExtractedField, ...]
    raw_output: str
    model: str
    backend: str
    prompt_version: str
    duration_ms: int


class Extractor(Protocol):
    """抽取器接口：任何类只要有 name 属性和 extract 方法，就是一个抽取器。"""

    name: str

    def extract(self, page: OcrPage, fields: Sequence[FieldSpec]) -> ExtractionResult:
        """从一页 OCR 结果里抽出指定的字段。"""
        ...
