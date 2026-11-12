"""大模型抽取器：把页面结构和字段清单交给本地大模型，按 JSON Schema 拿回字段值。

给模型看的是页面结构（ADR 0034）：每个文字块带编号 ⟨n⟩、按页面上的位置排版（LAPDoc），
再附上版面区域、"标题 → 值"、表格。模型回答每个值和它用了哪几块（LMDX），证据对齐去那几块核对。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from jacara.plugins.extractors.base import (
    ExtractedField,
    ExtractionError,
    ExtractionResult,
    FieldSpec,
    FieldType,
    TableSpec,
    cell_name,
    kv_name,
)
from jacara.plugins.model_backends import ChatMessage, ModelBackend
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.ocr.layout import continuation_rows, group_rows
from jacara.structure.analyze import analyze_page
from jacara.structure.graph import PageStructure
from jacara.structure.serialize import spatial_text, structure_text

PROMPT_VERSION = "extract-v6"
"""提示词的版本号。改了下面的提示词就要改这个号，结果里会记录它。

v1：OCR 文字逐块拼接。v2：按视觉行合并、加行号，并要求模型给出值所在的行号。
v3：可以抽表格（商品明细），表格一行一个对象，每一格也给行号。
v4：表格每行只给一个行号、格子直接写值（回答短一半多）。
    v3 在商品多的收据上回答超过 4096 个 token 被截断。行数不设上限：表格必须完整。
v5：折行的值：值跨几行时连起来写，给 row 和 row_end（ADR 0033）。
v6：按文字块（ADR 0034）：每块带编号、按版面原样排开，附上版面区域、标题 → 值、表格；
    每个值回答用了哪几块（blocks），不再给行号。
"""

SYSTEM_PROMPT = """你是文档信息抽取助手。用户会给你一张单据的文字和要抽取的字段。
文字是按单据上的位置排开的：同一行的几块左右排开，上下对齐的就是一列，
每块文字前面的 ⟨数字⟩ 是这块的编号。
文字后面可能还有按位置找出来的"版面区域""标题 → 值""表格"，帮你看清结构，但不一定全对，以原文为准。
规则：
1. 只根据给出的文字回答，不要猜，不要补充文字里没有的信息。
2. 每个字段回答 value 和 blocks：blocks 是这个值用到的文字块编号（⟨数字⟩ 里的数字），按原文顺序。
   找不到的字段，value 填 null，blocks 填 []。
3. 文字类字段照抄原文，包括看起来像 OCR 错字的地方，不要改写或纠正。
   值跨好几块（地址、长的商品名折成两行）时，把这几块里属于它的文字按顺序连起来（中间加一个空格），
   blocks 写这几块。一块里只有一部分是值（"Date: 31/12/2017" 里的日期），
   blocks 写这一块，value 只写值。
4. 数字字段只填数字，不带货币符号和千分位逗号。
5. 日期字段统一写成 YYYY-MM-DD。
6. 看清值在哪个标题下面、属于哪一栏：例如 Rooms 下面的数字是房间数，不是晚数。
7. 表格按原文顺序，一行一个对象，每一格回答 value 和 blocks；没有表格就给空列表。
   不要把小计、税、总额、找零当成表格的行，同一行商品不要重复写。商品名折成两行仍是同一行商品。"""

_TYPE_NAMES = {"string": "文字", "number": "数字", "date": "日期"}

_BLOCKS: dict[str, object] = {"type": "array", "items": {"type": "integer"}}
"""用到的文字块编号。"""

DISCOVER_PROMPT_VERSION = "discover-v3"
"""键值对发现的提示词版本。v2：值跨几行（row_end）。v3：按文字块（blocks），和 extract-v6 一样。"""

DISCOVER_PROMPT = """你是文档信息抽取助手。用户会给你一张单据的文字。
文字是按单据上的位置排开的，每块文字前面的 ⟨数字⟩ 是这块的编号；后面可能还有按位置找出来的
"标题 → 值"和表格，帮你看清结构，以原文为准。
任务：找出单据上所有"名称：值"形式的信息（例如 发票号码、日期、合计、地址、电话），
每一项给出 key（单据上印的名称，照抄原文）、value（值，照抄原文）、type、
blocks（值用到的文字块编号）。
规则：
1. 只根据给出的文字回答，不要猜，不要补充文字里没有的信息。
2. key 用单据上的原话；单据上没有印名称、但意思明确的值（例如最上面的商家名），
   key 用简短的中文说明，例如"商家名称"。值写在标题下面的，key 用那个标题。
3. type：金额、数量这类纯数字写 number，value 只填数字，不带货币符号和千分位逗号；
   日期写 date，value 写成 YYYY-MM-DD；其他写 string，value 照抄原文。
4. 值跨几块（例如地址折成两行）时连起来写（中间加一个空格），blocks 写这几块。
5. 商品明细这种表格不要逐格列出，每一行商品也不要当成键值对；同一项不要重复写。"""

DISCOVER_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}]},
                    "type": {"enum": ["string", "number", "date"]},
                    "blocks": _BLOCKS,
                },
                "required": ["key", "value", "type", "blocks"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["pairs"],
    "additionalProperties": False,
}


def _plain_schema(f: FieldSpec) -> dict[str, object]:
    """只有值：数字、日期或文字，或者 null。"""
    if f.type == "number":
        value: dict[str, object] = {"type": "number"}
    elif f.type == "date":
        value = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
    else:
        value = {"type": "string"}
    return {"anyOf": [value, {"type": "null"}]}


def _value_schema(f: FieldSpec) -> dict[str, object]:
    """一个值：{"value": 值或 null, "blocks": [文字块编号]}，两个都必须有。"""
    return {
        "type": "object",
        "properties": {"value": _plain_schema(f), "blocks": _BLOCKS},
        "required": ["value", "blocks"],
        "additionalProperties": False,
    }


def _row_schema(columns: Sequence[FieldSpec]) -> dict[str, object]:
    """表格的一行：{列名: {"value", "blocks"}, ...}。"""
    return {
        "type": "object",
        "properties": {c.name: _value_schema(c) for c in columns},
        "required": [c.name for c in columns],
        "additionalProperties": False,
    }


def build_schema(
    fields: Sequence[FieldSpec], tables: Sequence[TableSpec] = ()
) -> dict[str, object]:
    """把字段清单变成 JSON Schema：每个字段 {"value", "blocks"}，每张表一个列表。都必须出现。"""
    properties: dict[str, object] = {f.name: _value_schema(f) for f in fields}
    for t in tables:
        properties[t.name] = {"type": "array", "items": _row_schema(t.columns)}
    return {
        "type": "object",
        "properties": properties,
        "required": [*(f.name for f in fields), *(t.name for t in tables)],
        "additionalProperties": False,
    }


def _field_line(f: FieldSpec, indent: str = "") -> str:
    return f"{indent}- {f.name}（{_TYPE_NAMES[f.type]}）：{f.description}"


def page_prompt(structure: PageStructure) -> str:
    """给模型看的单据：版面原样 + 结构。"""
    text = f"单据文字（按位置排开，⟨数字⟩ 是文字块编号）：\n{spatial_text(structure)}"
    extra = structure_text(structure)
    return f"{text}\n\n{extra}" if extra else text


def build_messages(
    page: OcrPage,
    fields: Sequence[FieldSpec],
    tables: Sequence[TableSpec] = (),
    structure: PageStructure | None = None,
) -> list[ChatMessage]:
    """拼出发给模型的消息：系统规则 + 字段和表格说明 + 单据（版面原样 + 结构）。"""
    structure = structure or analyze_page(page)
    field_lines = "\n".join(_field_line(f) for f in fields)
    for t in tables:
        columns = "\n".join(_field_line(c, "  ") for c in t.columns)
        field_lines += f"\n- {t.name}（表格，每行一个对象）：{t.description}\n{columns}"
    user = f"要抽取的字段：\n{field_lines}\n\n{page_prompt(structure)}"
    return [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", user)]


class LlmExtractor:
    name = "llm"

    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    def extract(
        self,
        page: OcrPage,
        fields: Sequence[FieldSpec],
        tables: Sequence[TableSpec] = (),
        structure: PageStructure | None = None,
    ) -> ExtractionResult:
        structure = structure or analyze_page(page)
        schema = build_schema(fields, tables)
        result = self.backend.chat(
            build_messages(page, fields, tables, structure), json_schema=schema
        )
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as err:
            raise ExtractionError(f"模型的回答不是合格的 JSON：{result.text[:200]}") from err
        if not isinstance(data, dict):
            raise ExtractionError(f"模型的回答不是 JSON 对象：{result.text[:200]}")
        data = cast(dict[str, Any], data)

        extracted = [_field(f, data.get(f.name), f.name, result.text, structure) for f in fields]
        marks = continuation_rows(group_rows(page))
        for t in tables:
            rows = data.get(t.name, [])
            if not isinstance(rows, list):
                raise ExtractionError(f"模型的回答里 {t.name} 不是列表：{result.text[:200]}")
            items: list[list[ExtractedField]] = []
            for row in cast(list[Any], rows):
                if not isinstance(row, dict):
                    raise ExtractionError(f"模型的回答里 {t.name} 有一行不是对象")
                row = cast(dict[str, Any], row)
                if not all(c.name in row for c in t.columns):
                    raise ExtractionError(f"模型的回答里 {t.name} 有一行缺了列：{row}")
                cells = [
                    _field(c, _cell(row, c.name), c.name, result.text, structure) for c in t.columns
                ]
                if all(c.value is None for c in cells):
                    continue  # 整行都是空的，不算一行
                if items and _continues_item(items[-1], cells, t, marks):
                    items[-1] = _join_item(items[-1], cells, structure)
                    continue
                items.append(cells)
            for index, cells in enumerate(items, start=1):
                extracted += [
                    _renamed(cell, cell_name(t.name, index, c.name))
                    for cell, c in zip(cells, t.columns, strict=True)
                ]

        return ExtractionResult(
            fields=tuple(extracted),
            raw_output=result.text,
            model=result.model,
            backend=result.backend,
            prompt_version=PROMPT_VERSION,
            duration_ms=result.duration_ms,
        )

    def discover(self, page: OcrPage, structure: PageStructure | None = None) -> ExtractionResult:
        """键值对发现（"用样例建模板"的第一步）：找出单据上所有的"名称：值"。"""
        return _discover(self, page, structure or analyze_page(page))


def _discover(self: LlmExtractor, page: OcrPage, structure: PageStructure) -> ExtractionResult:
    messages = [
        ChatMessage("system", DISCOVER_PROMPT),
        ChatMessage("user", page_prompt(structure)),
    ]
    result = self.backend.chat(messages, json_schema=DISCOVER_SCHEMA)
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError as err:
        raise ExtractionError(f"模型的回答不是合格的 JSON：{result.text[:200]}") from err
    pairs = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs, list):
        raise ExtractionError(f"模型的回答里没有 pairs 列表：{result.text[:200]}")
    fields: list[ExtractedField] = []
    seen: set[tuple[str, str]] = set()
    for item in cast(list[Any], pairs):
        if not isinstance(item, dict):
            continue
        item = cast(dict[str, Any], item)
        key = str(item.get("key") or "").strip()
        value = item.get("value")
        if not key or value is None or str(value).strip() == "":
            continue
        type_: FieldType = item["type"] if item.get("type") in _TYPE_NAMES else "string"
        if type_ == "number":
            value = _as_number(value)
            if value is None:
                type_, value = "string", str(item.get("value"))
        else:
            value = str(value).strip()
        if (key, str(value)) in seen:
            continue  # 同一项写了两遍
        seen.add((key, str(value)))
        blocks = _blocks(item, structure)
        row, row_end = _rows_of(blocks, structure) if blocks else _legacy_rows(item)
        fields.append(
            ExtractedField(
                name=kv_name(len(fields) + 1),
                type=type_,
                value=value,
                row=row,
                label=key[:200],
                row_end=row_end,
                blocks=blocks,
            )
        )
    return ExtractionResult(
        fields=tuple(fields),
        raw_output=result.text,
        model=result.model,
        backend=result.backend,
        prompt_version=DISCOVER_PROMPT_VERSION,
        duration_ms=result.duration_ms,
    )


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    from jacara.core.textvalue import parse_number

    return parse_number(str(value))


def _blocks(item: dict[str, Any], structure: PageStructure) -> tuple[int, ...]:
    """模型给的文字块编号：只留存在的、不重复的，按原文顺序（编号就是阅读顺序）。"""
    raw = item.get("blocks")
    if not isinstance(raw, list):
        return ()
    found = {
        n
        for n in cast(list[Any], raw)
        if isinstance(n, int) and not isinstance(n, bool) and structure.word(n) is not None
    }
    return tuple(sorted(found))


def _rows_of(blocks: tuple[int, ...], structure: PageStructure) -> tuple[int | None, int | None]:
    """文字块 → (第一行, 最后一行)。只在一行里时最后一行是 None。"""
    rows = sorted(int(w.attrs["row"]) for n in blocks if (w := structure.word(n)) is not None)
    if not rows:
        return None, None
    return rows[0], (rows[-1] if rows[-1] != rows[0] else None)


def _field(
    spec: FieldSpec, item: object, name: str, raw: str, structure: PageStructure
) -> ExtractedField:
    """模型回答里的一个 {"value", "blocks"} → ExtractedField。name 是存下来的字段名。"""
    if not isinstance(item, dict) or "value" not in item:
        raise ExtractionError(f"模型的回答缺少字段 {name}：{raw[:200]}")
    item = cast(dict[str, Any], item)
    value = item["value"]
    if spec.type == "number" and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    blocks = _blocks(item, structure) if value is not None else ()
    row, row_end = _rows_of(blocks, structure)
    if not blocks and value is not None:
        row, row_end = _legacy_rows(item)
    return ExtractedField(
        name=name, type=spec.type, value=value, row=row, row_end=row_end, blocks=blocks
    )


def _cell(row: dict[str, Any], column: str) -> object:
    """表格的一格。模型偶尔照老格式回答（格子直接写值，行号写在这一行的 row 上），也能读。"""
    cell = row[column]
    if isinstance(cell, dict):
        return cell
    return {"value": cell, "row": row.get("row"), "row_end": row.get("row_end")}


def _legacy_rows(item: dict[str, Any]) -> tuple[int | None, int | None]:
    """模型没给文字块、却照老格式给了行号（extract-v5 的 row、row_end）：留着当线索，按文字去找。"""

    def number(value: object) -> int | None:
        ok = isinstance(value, int) and not isinstance(value, bool) and value > 0
        return cast(int, value) if ok else None

    row, end = number(item.get("row")), number(item.get("row_end"))
    return row, (end if row is not None and end is not None and end > row else None)


def _renamed(cell: ExtractedField, name: str) -> ExtractedField:
    return ExtractedField(
        name=name,
        type=cell.type,
        value=cell.value,
        row=cell.row,
        label=cell.label,
        row_end=cell.row_end,
        blocks=cell.blocks,
    )


def _continues_item(
    previous: list[ExtractedField],
    row: list[ExtractedField],
    table: TableSpec,
    marks: frozenset[int],
) -> bool:
    """模型还是把折行的商品名拆成了两行商品：这一"行"只有文字、没有任何数字，
    而且它就在上一行商品的下一行、按位置看是接着上一行写的（行号在 marks 里）。

    例如上一行 "Room Charges Deluxe  1  1939  1939"，这一行只有 "Double with Breakfast"。
    小节标题（"饮料"）在商品上面、不是接着上一行写的，不会被并进去。
    """
    lines = [c.row for c in row if c.row is not None]
    ends = [end for c in previous if (end := c.row_end or c.row) is not None]
    last = max(ends, default=None)
    if not lines or last is None or min(lines) != last + 1 or min(lines) not in marks:
        return False
    texts = [i for i, c in enumerate(table.columns) if c.type == "string"]
    others = [i for i, c in enumerate(table.columns) if c.type != "string"]
    return any(row[i].value is not None for i in texts) and all(
        row[i].value is None for i in others
    )


def _join_item(
    previous: list[ExtractedField], row: list[ExtractedField], structure: PageStructure
) -> list[ExtractedField]:
    """把折行的那一行并进上一行商品：文字接在后面（中间一个空格），文字块也并进去。"""
    joined: list[ExtractedField] = []
    for before, extra in zip(previous, row, strict=True):
        if before.type == "string" and extra.value is not None:
            value = f"{before.value} {extra.value}" if before.value else extra.value
            blocks = tuple(sorted({*before.blocks, *extra.blocks}))
            if blocks:
                row_no, row_end = _rows_of(blocks, structure)
            else:  # 老格式（只有行号）：第一行不变，最后一行是这一行
                row_no, row_end = before.row, extra.row_end or extra.row
            joined.append(
                ExtractedField(
                    name=before.name,
                    type=before.type,
                    value=value,
                    row=row_no,
                    row_end=row_end,
                    blocks=blocks,
                )
            )
        else:
            joined.append(before)
    return joined
