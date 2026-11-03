"""大模型抽取器：把 OCR 文字和字段清单交给本地大模型，按 JSON Schema 拿回字段值。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from jacara.plugins.extractors.base import (
    ExtractedField,
    ExtractionError,
    ExtractionResult,
    FieldSpec,
)
from jacara.plugins.model_backends import ChatMessage, ModelBackend
from jacara.plugins.ocr.base import OcrPage
from jacara.plugins.ocr.layout import group_rows, numbered_text

PROMPT_VERSION = "extract-v2"
"""提示词的版本号。改了下面的提示词就要改这个号，结果里会记录它。

v1：OCR 文字逐块拼接。v2：按视觉行合并、加行号，并要求模型给出值所在的行号。
"""

SYSTEM_PROMPT = """你是文档信息抽取助手。用户会给你一张单据的 OCR 文字和要抽取的字段。
OCR 文字每行开头的 [数字] 是行号。
规则：
1. 只根据给出的文字回答，不要猜，不要补充文字里没有的信息。
2. 文字里找不到的字段，value 和 row 都填 null。
3. 文字类字段照抄原文，包括看起来像 OCR 错字的地方，不要改写或纠正。
4. 数字字段只填数字，不带货币符号和千分位逗号。
5. 日期字段统一写成 YYYY-MM-DD。
6. row 填这个值所在行的行号。"""

_TYPE_NAMES = {"string": "文字", "number": "数字", "date": "日期"}


def build_schema(fields: Sequence[FieldSpec]) -> dict[str, object]:
    """把字段清单变成 JSON Schema。

    每个字段是 {"value": 值或 null, "row": 行号或 null}，每个字段都必须出现。
    """
    properties: dict[str, object] = {}
    for f in fields:
        if f.type == "number":
            value: dict[str, object] = {"type": "number"}
        elif f.type == "date":
            value = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
        else:
            value = {"type": "string"}
        properties[f.name] = {
            "type": "object",
            "properties": {
                "value": {"anyOf": [value, {"type": "null"}]},
                "row": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            },
            "required": ["value", "row"],
            "additionalProperties": False,
        }
    return {
        "type": "object",
        "properties": properties,
        "required": [f.name for f in fields],
        "additionalProperties": False,
    }


def build_messages(page: OcrPage, fields: Sequence[FieldSpec]) -> list[ChatMessage]:
    """拼出发给模型的消息：系统规则 + 字段说明 + 带行号的 OCR 文字。"""
    field_lines = "\n".join(f"- {f.name}（{_TYPE_NAMES[f.type]}）：{f.description}" for f in fields)
    text = numbered_text(group_rows(page))
    user = f"要抽取的字段：\n{field_lines}\n\nOCR 文字：\n{text}"
    return [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", user)]


class LlmExtractor:
    name = "llm"

    def __init__(self, backend: ModelBackend) -> None:
        self.backend = backend

    def extract(self, page: OcrPage, fields: Sequence[FieldSpec]) -> ExtractionResult:
        result = self.backend.chat(build_messages(page, fields), json_schema=build_schema(fields))
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError as err:
            raise ExtractionError(f"模型的回答不是合格的 JSON：{result.text[:200]}") from err
        if not isinstance(data, dict):
            raise ExtractionError(f"模型的回答不是 JSON 对象：{result.text[:200]}")

        extracted: list[ExtractedField] = []
        for f in fields:
            item = data.get(f.name)
            if not isinstance(item, dict) or "value" not in item:
                raise ExtractionError(f"模型的回答缺少字段 {f.name}：{result.text[:200]}")
            value = item["value"]
            if f.type == "number" and isinstance(value, int):
                value = float(value)
            row = item.get("row")
            extracted.append(
                ExtractedField(
                    name=f.name,
                    type=f.type,
                    value=value,
                    row=row if isinstance(row, int) else None,
                )
            )

        return ExtractionResult(
            fields=tuple(extracted),
            raw_output=result.text,
            model=result.model,
            backend=result.backend,
            prompt_version=PROMPT_VERSION,
            duration_ms=result.duration_ms,
        )
