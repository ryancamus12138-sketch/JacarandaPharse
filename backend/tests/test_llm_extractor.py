"""LLM extractor, tested with a fake model backend; no llama-server required."""

import json

from jacara.plugins.extractors import (
    ExtractionError,
    FieldSpec,
    LlmExtractor,
    build_schema,
)
from jacara.plugins.model_backends import ChatMessage, ChatResult
from jacara.templates import load_template

from .conftest import raises

RECEIPT_FIELDS = load_template("receipt").specs


class FakeBackend:
    """Fake model backend: answers every request with prepared text. Implements ModelBackend."""

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
        "total": (9.0, 6),  # The integer 9 becomes 9.0.
    }
    assert result.model == "fake-model"
    assert result.prompt_version == "extract-v8"
    assert result.raw_output == backend.answer  # The raw answer is kept verbatim.


def test_prompt_contains_numbered_rows(receipt_page):
    backend = FakeBackend(answer(total=(9.0, 6)))
    LlmExtractor(backend).extract(receipt_page, [FieldSpec("total", "number", "总额")])
    user_message = backend.received[-1].content
    # Each block indexed and laid out by position; "Date:" and the date form a label → value pair.
    assert "⟨4⟩Date:" in user_message and "⟨5⟩25/12/2018 8:13:39 PM" in user_message
    assert "⟨4⟩Date → ⟨5⟩25/12/2018 8:13:39 PM" in user_message
    assert backend.schema == build_schema([FieldSpec("total", "number", "总额")])


def test_invalid_json_raises(receipt_page):
    with raises(ExtractionError, "不是合格的 JSON"):
        LlmExtractor(FakeBackend("好的，总额是 9 元")).extract(receipt_page, RECEIPT_FIELDS)


def test_missing_field_raises(receipt_page):
    backend = FakeBackend(answer(merchant=("SHOP", 1)))
    with raises(ExtractionError, "缺少字段"):
        LlmExtractor(backend).extract(receipt_page, RECEIPT_FIELDS)


def test_schema_types():
    schema = build_schema([FieldSpec("total", "number", ""), FieldSpec("date", "date", "")])
    properties = schema["properties"]
    assert isinstance(properties, dict)
    total = properties["total"]["properties"]["value"]["anyOf"]
    assert {"type": "number"} in total
    assert {"type": "null"} in total
    assert schema["additionalProperties"] is False
