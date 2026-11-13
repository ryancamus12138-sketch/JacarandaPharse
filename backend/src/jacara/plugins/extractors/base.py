"""字段抽取扩展点：规定"抽什么字段"和"抽出来的结果长什么样"。

抽取器拿到 OCR 结果，按字段清单填值。后面的证据对齐、规则校验只认这里的类型，
不关心值是大模型抽的、规则抽的，还是以后别的方法。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from jacara.plugins.ocr.base import OcrPage

FieldType = Literal["string", "number", "date", "date_range"]
"""date_range：日期范围（ADR 0039），值是照抄的原文，开始、结束日期由代码拆出来。"""


class ExtractionError(RuntimeError):
    """模型的回答不是合格的 JSON，或缺了字段。"""


@dataclass(frozen=True)
class FieldSpec:
    """要抽取的一个字段：名字、类型、给模型看的说明。"""

    name: str
    type: FieldType
    description: str


@dataclass(frozen=True)
class TableSpec:
    """要抽取的一张表（例如商品明细）：每行有同样的几列，行数不固定。"""

    name: str
    description: str
    columns: tuple[FieldSpec, ...]


_CELL = re.compile(r"^([a-z][a-z0-9_]*)\.(\d+)\.([a-z][a-z0-9_]*)$")


def cell_name(table: str, index: int, column: str) -> str:
    """表格里一格的字段名：items.2.amount 是商品明细第 2 行的金额。行号从 1 开始。

    表格的每一格都当作一个普通字段：找出处、规则校验、人工审核、修改历史都不用另写一套。
    """
    return f"{table}.{index}.{column}"


def split_cell_name(name: str) -> tuple[str, int, str] | None:
    """cell_name 的反方向：items.2.amount → ("items", 2, "amount")。普通字段返回 None。"""
    m = _CELL.match(name)
    return (m[1], int(m[2]), m[3]) if m else None


@dataclass(frozen=True)
class ExtractedField:
    """抽出来的一个字段。value 为 None 表示原文里找不到。

    row 是模型说的"值在第几行"，只是线索，要由证据对齐去核对。
    """

    name: str
    """字段名。表格里的格子是 items.2.amount 这样的名字（见 cell_name）。"""
    type: FieldType
    value: str | float | None
    row: int | None = None
    label: str | None = None
    """单据上的原名（键值对发现时才有，例如 kv.3 是"Check-in"）。"""
    row_end: int | None = None
    """值跨几行时（商品名、地址折行），最后一行的行号。只占一行时是 None。"""
    blocks: tuple[int, ...] = ()
    """模型说这个值用了哪几个文字块（编号，ADR 0034）。证据对齐按它核对。"""
    text: str | None = None
    """原文里这个值一字不差的写法（ADR 0040）：数字、日期、日期范围才有，
    例如 value 是 2014-08-03，text 是 "August 3 , 2014"。
    证据对齐先按 text 在原文里找，再看 value 和它对不对得上。"""


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
    vision: str | None = None
    """这次抽取给模型看了原图（ADR 0038），写着为什么看；没看图是 None。"""
    sent: Mapping[str, Any] | None = None
    """这次发给模型的是什么（ADR 0053）：哪几个文字块（编号）、发了多少字、几张图、
    几个以前改过的例子、发出去的单据文字原文和它的 sha256。追查"模型为什么没取到"时用：
    值在不在发出去的文字里，决定是 OCR 的问题还是抽取的问题。"""


class Extractor(Protocol):
    """抽取器接口：任何类只要有 name 属性和 extract 方法，就是一个抽取器。"""

    name: str

    def extract(
        self,
        page: OcrPage,
        fields: Sequence[FieldSpec],
        tables: Sequence[TableSpec] = (),
        structure: PageStructure | None = None,
        images: Sequence[bytes] = (),
    ) -> ExtractionResult:
        """从一页 OCR 结果里抽出指定的字段和表格。表格的每一格单独作为一个字段返回。

        structure 是这一页的结构（文字块编号、表格、标题 → 值）；不给就只按规则分析一次。
        images 是单据原图（PNG，每页一张，ADR 0038）：给了就让模型看着图抽。"""
        ...

    def discover(self, page: OcrPage, structure: PageStructure | None = None) -> ExtractionResult:
        """不给字段清单，找出单据上所有的"名称：值"。字段名是 kv.1、kv.2……，label 是原名。"""
        ...


if TYPE_CHECKING:
    from jacara.structure.graph import PageStructure

DISCOVER = "discover"
"""键值对发现的"模板名"：存在 extractions.template 里，表示这一轮没有用模板。"""

AUTO = "auto"
"""上传时选"自动判断"（ADR 0038）：每张单据从我的模板里挑最合适的一个。"""


def kv_name(index: int) -> str:
    """键值对发现的第 index 个（从 1 开始）的字段名。"""
    return f"kv.{index}"
