"""大模型抽取器：用一个假的模型后端测试，不需要启动 llama-server。"""

import json

import pytest

from jacara.plugins.extractors import (
    ExtractionError,
    FieldSpec,
    LlmExtractor,
    build_schema,
)
from jacara.plugins.model_backends import ChatMessage, ChatResult
from jacara.templates import load_template

RECEIPT_FIELDS = load_template("receipt").specs


class FakeBackend:
    """假的模型后端：不管问什么，都回答事先准备好的文字。它满足 ModelBackend 接口。"""

    name = "fake"

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.received: list[ChatMessage] = []
        self.schema: dict[str, object] | None = None

    def chat(self, messages, *, json_schema=None):
        self.received = list(messages)
        self.schema = json_schema
        return ChatResult(self.answer, "fake-model", self.name, 0, 0, 1)


def answer(**fields: tuple[object, int | None]) -> str:
    return json.dumps({k: {"value": v, "row": r} for k, (v, r) in fields.items()})


def test_values_rows_and_provenance(receipt_page):
    backend = FakeBackend(
        answer(
            merchant=("SHOP", 1),
            store_name=(None, None),
            document_no=(None, None),
            date=("2018-12-25", 4),
            total=(9, 6),
        )
    )
    result = LlmExtractor(backend).extract(receipt_page, RECEIPT_FIELDS)

    values = {f.name: (f.value, f.row) for f in result.fields}
    assert values == {
        "merchant": ("SHOP", 1),
        "store_name": (None, None),
        "document_no": (None, None),
        "date": ("2018-12-25", 4),
        "total": (9.0, 6),  # 整数 9 转成 9.0
    }
    assert result.model == "fake-model"
    assert result.prompt_version == "extract-v4"
    assert result.raw_output == backend.answer  # 原始回答原样保留


def test_prompt_contains_numbered_rows(receipt_page):
    backend = FakeBackend(answer(total=(9.0, 6)))
    LlmExtractor(backend).extract(receipt_page, [FieldSpec("total", "number", "总额")])
    user_message = backend.received[-1].content
    assert "[4] Date:  25/12/2018 8:13:39 PM" in user_message
    assert backend.schema == build_schema([FieldSpec("total", "number", "总额")])


def test_invalid_json_raises(receipt_page):
    with pytest.raises(ExtractionError, match="不是合格的 JSON"):
        LlmExtractor(FakeBackend("好的，总额是 9 元")).extract(receipt_page, RECEIPT_FIELDS)


def test_missing_field_raises(receipt_page):
    backend = FakeBackend(answer(merchant=("SHOP", 1)))
    with pytest.raises(ExtractionError, match="缺少字段"):
        LlmExtractor(backend).extract(receipt_page, RECEIPT_FIELDS)


def test_schema_types():
    schema = build_schema([FieldSpec("total", "number", ""), FieldSpec("date", "date", "")])
    properties = schema["properties"]
    assert isinstance(properties, dict)
    total = properties["total"]["properties"]["value"]["anyOf"]
    assert {"type": "number"} in total
    assert {"type": "null"} in total
    assert schema["additionalProperties"] is False
