"""字段双维度状态（ADR 0028）：证据 × 校验 → 红黄灰绿；采用原文值。

这张小收据上：商家名 OCR 读错了一个字（模型写对了，只能相似匹配）；DISC 1.00- 是折扣，
模型却抽成了正的 1.0，所以商品金额加起来 10.00 和总额 8.00 对不上；现金 12.00 原文里没有。
"""

import uuid
from datetime import datetime

import pytest

from jacara.core.db import session_scope
from jacara.core.textvalue import signed_number
from jacara.models import ExtractedValue, Extraction
from jacara.pipeline.current import evidence_text, level_of
from jacara.pipeline.store import save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator
from jacara.templates import load_template

from .conftest import make_line, make_page

PAGE = make_page(
    make_line("BOOK TA _K (TAMAN DAYA) SDN BHD", 68, 91, 423, 115),
    make_line("25/12/2018", 163, 169, 345, 190),
    make_line("PC", 71, 293, 113, 316),
    make_line("9.00", 408, 293, 447, 314),
    make_line("DISC", 71, 343, 113, 366),
    make_line("1.00-", 408, 343, 447, 364),
    make_line("Total", 241, 436, 300, 457),
    make_line("8.00", 407, 435, 446, 456),
)


@pytest.fixture
def document_id(factory, tmp_path) -> uuid.UUID:
    fields = (
        F("merchant", "string", "BOOK TAK (TAMAN DAYA) SDN BHD", 1),
        F("store_name", "string", None, None),
        F("date", "date", "2018-12-25", 2),
        F("total", "number", 8.0, 5),
        F("cash", "number", 12.0, 6),
        F("items.1.name", "string", "PC", 3),
        F("items.1.amount", "number", 9.0, 3),
        F("items.2.name", "string", "DISC", 4),
        F("items.2.amount", "number", 1.0, 4),
    )
    result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
    report = DocumentReport(
        source="receipt.jpg",
        extraction=result,
        aligned=TextMatchAligner().align(PAGE, result),
        checks=RuleValidator(load_template("receipt").rules).validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 12),
        ocr_page=PAGE,
    )
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        return save_document(session, path, [report]).id


def values(client, document_id) -> dict[str, dict]:
    extraction = client.get(f"/api/v1/documents/{document_id}").json()["extractions"][0]
    return {v["name"]: v for v in extraction["values"]}


def edit(client, value: dict, new: str, reason: str = "采用原文值"):
    body = {"action": "edit", "value": new, "reason": reason}
    response = client.post(f"/api/v1/values/{value['id']}/revisions", json=body)
    assert response.status_code == 201, response.text


def test_two_dimensions_and_colors(client, document_id):
    v = values(client, document_id)
    state = {name: (x["evidence_state"], x["check_state"], x["level"]) for name, x in v.items()}
    assert state["merchant"] == ("pending", "ok", "yellow")  # 只是相似
    assert state["store_name"] == ("empty", "ok", "grey")  # 原文没有
    assert state["date"] == ("verified", "ok", "green")
    assert state["total"] == ("verified", "ok", "green")
    assert state["cash"] == ("missing", "ok", "red")  # 原文里找不到
    assert state["items.1.amount"] == ("verified", "warn", "yellow")  # 合计对不上，金额都标出
    # 1.00- 是负数，值却是正的：出处有疑点，同时合计对不上
    assert state["items.2.amount"] == ("pending", "warn", "yellow")


def test_source_value_for_fuzzy_text_and_sign(client, document_id):
    v = values(client, document_id)
    assert v["merchant"]["source_value"] == "BOOK TA _K (TAMAN DAYA) SDN BHD"
    assert v["items.2.amount"]["source_value"] == "-1.0"
    # 完全一致的、找不到的、日期都没有原文值
    for name in ("date", "total", "cash", "items.1.amount", "items.1.name"):
        assert v[name]["source_value"] is None, name


def test_adopting_the_source_turns_things_green(client, document_id):
    edit(client, values(client, document_id)["items.2.amount"], "-1.0")
    v = values(client, document_id)
    # 9.00 − 1.00 = 8.00，合计对上了：两格金额都变绿；采用过之后就不再提示原文值
    assert v["items.1.amount"]["level"] == "green"
    assert v["items.2.amount"]["level"] == "green"
    assert v["items.2.amount"]["source_value"] is None


def test_confirming_does_not_hide_a_failing_rule(client, document_id):
    v = values(client, document_id)
    response = client.post(
        f"/api/v1/values/{v['items.1.amount']['id']}/revisions", json={"action": "confirm"}
    )
    assert response.status_code == 201
    # 人确认了 9.00 是对的，可合计还是对不上：照样标黄
    assert values(client, document_id)["items.1.amount"]["level"] == "yellow"
    # 找不到出处的值，人确认过就不再标红
    client.post(f"/api/v1/values/{v['cash']['id']}/revisions", json={"action": "confirm"})
    assert values(client, document_id)["cash"]["level"] == "green"


def test_error_rule_is_red_even_after_review():
    value = ExtractedValue(
        name="total",
        type="number",
        value="-8.0",
        status="matched",
        needs_review=False,
        note="",
        evidence={"bbox": [0, 0, 1, 1]},
    )
    value.revisions = []
    assert level_of(value, "fail") == "red"
    assert level_of(value, "warn") == "yellow"
    assert level_of(value, "ok") == "green"


def test_signed_number_only_takes_the_same_number():
    assert signed_number("DISC 1.00-", 1.0) == -1.0
    assert signed_number("(6.00)", 6.0) == -6.0
    assert signed_number("2 @ 3.00 6.00", 3.0) == 3.0
    assert signed_number("2 @ 3.00 6.00", 4.0) is None


def test_old_documents_find_the_text_in_the_page_ocr(client, factory, document_id):
    """更新前处理的文档出处里没存文字：用这一页的 OCR 结果按出处框找，数据库里的出处不改。"""
    with session_scope(factory) as session:
        for value in session.query(ExtractedValue):
            if value.evidence:
                value.evidence = {k: x for k, x in value.evidence.items() if k != "text"}
    v = values(client, document_id)
    assert v["merchant"]["source_value"] == "BOOK TA _K (TAMAN DAYA) SDN BHD"
    assert v["items.2.amount"]["source_value"] == "-1.0"
    assert "text" not in v["merchant"]["evidence"]
    with session_scope(factory) as session:
        extraction = session.query(Extraction).one()
        assert extraction.ocr is not None
        lines = extraction.ocr["lines"]
        total = next(x for x in extraction.values if x.name == "total")
        assert evidence_text(total, lines) == "8.00"
        assert evidence_text(total, None) is None  # 没存文字又没有 OCR 结果：不知道


def test_new_evidence_keeps_the_text(factory, document_id):
    with session_scope(factory) as session:
        total = session.query(ExtractedValue).filter_by(name="total").one()
        assert total.evidence is not None
        assert total.evidence["text"] == "8.00"
        assert evidence_text(total, None) == "8.00"  # 新文档不用读 OCR 结果
