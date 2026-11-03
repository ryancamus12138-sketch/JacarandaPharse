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

PROMPT_VERSION = "extract-v1"
"""提示词的版本号。改了下面的提示词就要改这个号，结果里会记录它。"""

SYSTEM_PROMPT = """你是文档信息抽取助手。用户会给你一张单据的 OCR 文字和要抽取的字段。
规则：
1. 只根据给出的文字回答，不要猜，不要补充文字里没有的信息。
2. 文字里找不到的字段填 null。
3. 文字类字段照抄原文，包括看起来像 OCR 错字的地方，不要改写或纠正。
4. 数字字段只填数字，不带货币符号和千分位逗号。
5. 日期字段统一写成 YYYY-MM-DD。"""

_TYPE_NAMES = {"string": "文字", "number": "数字", "date": "日期"}


def build_schema(fields: Sequence[FieldSpec]) -> dict[str, object]:
    """把字段清单变成 JSON Schema。每个字段都必须出现，值可以是 null。"""
    properties: dict[str, object] = {}
    for f in fields:
        if f.type == "number":
            value: dict[str, object] = {"type": "number"}
        elif f.type == "date":
            value = {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
        else:
            value = {"type": "string"}
        properties[f.name] = {"anyOf": [value, {"type": "null"}]}
    return {
        "type": "object",
        "properties": properties,
        "required": [f.name for f in fields],
        "additionalProperties": False,
    }


def build_messages(page: OcrPage, fields: Sequence[FieldSpec]) -> list[ChatMessage]:
    """拼出发给模型的消息：系统规则 + 字段说明 + OCR 全文。"""
    field_lines = "\n".join(f"- {f.name}（{_TYPE_NAMES[f.type]}）：{f.description}" for f in fields)
    user = f"要抽取的字段：\n{field_lines}\n\nOCR 文字：\n{page.text}"
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
            if f.name not in data:
                raise ExtractionError(f"模型的回答缺少字段 {f.name}：{result.text[:200]}")
            value = data[f.name]
            if f.type == "number" and isinstance(value, int):
                value = float(value)
            extracted.append(ExtractedField(name=f.name, type=f.type, value=value))

        return ExtractionResult(
            fields=tuple(extracted),
            raw_output=result.text,
            model=result.model,
            backend=result.backend,
            prompt_version=PROMPT_VERSION,
            duration_ms=result.duration_ms,
        )
