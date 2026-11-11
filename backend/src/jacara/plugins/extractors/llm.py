"""大模型抽取器：把 OCR 文字和字段清单交给本地大模型，按 JSON Schema 拿回字段值。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

from jacara.plugins.extractors.base import (
    ExtractedField,
    ExtractionError,
    ExtractionResult,
    FieldSpec,
    TableSpec,
    cell_name,
)
from jacara.plugins.model_backends import ChatMessage, ModelBackend
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.ocr.layout import group_rows, numbered_text

PROMPT_VERSION = "extract-v4"
"""提示词的版本号。改了下面的提示词就要改这个号，结果里会记录它。

v1：OCR 文字逐块拼接。v2：按视觉行合并、加行号，并要求模型给出值所在的行号。
v3：可以抽表格（商品明细），表格一行一个对象，每一格也给行号。
v4：表格每行只给一个行号、格子直接写值（回答短一半多）。
    v3 在商品多的收据上回答超过 4096 个 token 被截断。行数不设上限：表格必须完整。
"""

SYSTEM_PROMPT = """你是文档信息抽取助手。用户会给你一张单据的 OCR 文字和要抽取的字段。
OCR 文字每行开头的 [数字] 是行号。
规则：
1. 只根据给出的文字回答，不要猜，不要补充文字里没有的信息。
2. 文字里找不到的字段，value 和 row 都填 null。
3. 文字类字段照抄原文，包括看起来像 OCR 错字的地方，不要改写或纠正。
4. 数字字段只填数字，不带货币符号和千分位逗号。
5. 日期字段统一写成 YYYY-MM-DD。
6. row 填这个值所在行的行号。
7. 表格按原文顺序，一行一个对象，row 填这一行所在的行号；没有表格就给空列表。
   不要把小计、税、总额、找零当成表格的行，同一行商品不要重复写。"""

_TYPE_NAMES = {"string": "文字", "number": "数字", "date": "日期"}


_ROW: dict[str, object] = {"anyOf": [{"type": "integer"}, {"type": "null"}]}


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
    """一个值：{"value": 值或 null, "row": 行号或 null}，两个都必须有。"""
    return {
        "type": "object",
        "properties": {"value": _plain_schema(f), "row": _ROW},
        "required": ["value", "row"],
        "additionalProperties": False,
    }


def _row_schema(columns: Sequence[FieldSpec]) -> dict[str, object]:
    """表格的一行：{"row": 行号, 列名: 值, ...}。一行的格子都在同一行，行号只写一次。"""
    properties: dict[str, object] = {"row": _ROW}
    properties.update({c.name: _plain_schema(c) for c in columns})
    return {
        "type": "object",
        "properties": properties,
        "required": ["row", *(c.name for c in columns)],
        "additionalProperties": False,
    }


def _object_schema(fields: Sequence[FieldSpec]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {f.name: _value_schema(f) for f in fields},
        "required": [f.name for f in fields],
        "additionalProperties": False,
    }


def build_schema(
    fields: Sequence[FieldSpec], tables: Sequence[TableSpec] = ()
) -> dict[str, object]:
    """把字段清单变成 JSON Schema。

    每个字段是 {"value", "row"}；每张表是一个列表，列表里每一行是 {"row": 行号, 列名: 值}。
    所有字段和表都必须出现。
    """
    schema = _object_schema(fields)
    properties = cast(dict[str, object], schema["properties"])
    for t in tables:
        properties[t.name] = {"type": "array", "items": _row_schema(t.columns)}
    schema["required"] = [*(f.name for f in fields), *(t.name for t in tables)]
    return schema


def _field_line(f: FieldSpec, indent: str = "") -> str:
    return f"{indent}- {f.name}（{_TYPE_NAMES[f.type]}）：{f.description}"


def build_messages(
    page: OcrPage, fields: Sequence[FieldSpec], tables: Sequence[TableSpec] = ()
) -> list[ChatMessage]:
    """拼出发给模型的消息：系统规则 + 字段和表格说明 + 带行号的 OCR 文字。"""
    field_lines = "\n".join(_field_line(f) for f in fields)
    for t in tables:
        columns = "\n".join(_field_line(c, "  ") for c in t.columns)
        field_lines += f"\n- {t.name}（表格，每行一个对象，带 row）：{t.description}\n{columns}"
    text = numbered_text(group_rows(page))
    user = f"要抽取的字段：\n{field_lines}\n\nOCR 文字：\n{text}"
    return [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", user)]


class LlmExtractor:
    name = "llm"

    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    def extract(
        self, page: OcrPage, fields: Sequence[FieldSpec], tables: Sequence[TableSpec] = ()
    ) -> ExtractionResult:
        schema = build_schema(fields, tables)
        result = self.backend.chat(build_messages(page, fields, tables), json_schema=schema)
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as err:
            raise ExtractionError(f"模型的回答不是合格的 JSON：{result.text[:200]}") from err
        if not isinstance(data, dict):
            raise ExtractionError(f"模型的回答不是 JSON 对象：{result.text[:200]}")
        data = cast(dict[str, Any], data)

        extracted = [_field(f, data.get(f.name), f.name, result.text) for f in fields]
        for t in tables:
            rows = data.get(t.name, [])
            if not isinstance(rows, list):
                raise ExtractionError(f"模型的回答里 {t.name} 不是列表：{result.text[:200]}")
            index = 0
            for row in cast(list[Any], rows):
                if not isinstance(row, dict):
                    raise ExtractionError(f"模型的回答里 {t.name} 有一行不是对象")
                row = cast(dict[str, Any], row)
                if not all(c.name in row for c in t.columns):
                    raise ExtractionError(f"模型的回答里 {t.name} 有一行缺了列：{row}")
                line = row.get("row")
                cells = [
                    _field(
                        c,
                        {"value": row[c.name], "row": line},
                        cell_name(t.name, index + 1, c.name),
                        result.text,
                    )
                    for c in t.columns
                ]
                if all(c.value is None for c in cells):
                    continue  # 整行都是空的，不算一行
                index += 1
                extracted += cells

        return ExtractionResult(
            fields=tuple(extracted),
            raw_output=result.text,
            model=result.model,
            backend=result.backend,
            prompt_version=PROMPT_VERSION,
            duration_ms=result.duration_ms,
        )


def _field(spec: FieldSpec, item: object, name: str, raw: str) -> ExtractedField:
    """模型回答里的一个 {"value", "row"} → ExtractedField。name 是存下来的字段名。"""
    if not isinstance(item, dict) or "value" not in item:
        raise ExtractionError(f"模型的回答缺少字段 {name}：{raw[:200]}")
    item = cast(dict[str, Any], item)
    value = item["value"]
    if spec.type == "number" and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    row = item.get("row")
    return ExtractedField(
        name=name,
        type=spec.type,
        value=value,
        row=row if isinstance(row, int) and not isinstance(row, bool) else None,
    )
