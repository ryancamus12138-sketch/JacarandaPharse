"""商品明细（表格字段）：抽取、找出处、规则、存库读回、导出。

表格的每一格都是一个普通字段，名字是 items.2.amount（第 2 行的金额）。
"""

import json
from datetime import datetime

import pytest
from openpyxl import load_workbook

from jacara.core.db import session_scope
from jacara.models import Document
from jacara.pipeline.load import load_reports
from jacara.pipeline.store import save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport, ExcelExporter
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import (
    ExtractionError,
    ExtractionResult,
    FieldSpec,
    LlmExtractor,
    TableSpec,
    build_schema,
    cell_name,
    split_cell_name,
)
from jacara.plugins.model_backends import ChatResult
from jacara.plugins.validators import RuleValidator
from jacara.templates import load_template

ITEMS = load_template("receipt").table_specs[0]
TOTAL = [FieldSpec("total", "number", "总额")]


class FakeBackend:
    name = "fake"

    def __init__(self, answer: object) -> None:
        self.answer = json.dumps(answer)
        self.messages: list = []
        self.schema: dict | None = None

    def chat(self, messages, *, json_schema=None):
        self.messages = list(messages)
        self.schema = json_schema
        return ChatResult(self.answer, "fake-model", self.name, 0, 0, 1)


def cell(value: object, row: int | None = 5) -> dict[str, object]:
    return {"value": value, "row": row}


def item(name: object, quantity: object, unit_price: object, amount: object, row=5) -> dict:
    """表格的一行：行号只写一次，格子直接写值（提示词 v4）。"""
    return {
        "row": row,
        "name": name,
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": amount,
    }


def test_cell_names_go_both_ways():
    assert cell_name("items", 2, "amount") == "items.2.amount"
    assert split_cell_name("items.2.unit_price") == ("items", 2, "unit_price")
    assert split_cell_name("total") is None
    assert split_cell_name("items.x.amount") is None


def test_schema_has_a_list_for_each_table():
    schema = build_schema(TOTAL, [ITEMS])
    assert schema["required"] == ["total", "items"]
    items = schema["properties"]["items"]  # type: ignore[index]
    assert items["type"] == "array"
    assert "maxItems" not in items  # 行数不设上限：表格必须完整
    assert items["items"]["required"] == ["name", "quantity", "unit_price", "amount"]
    # 没有表格时和以前一模一样
    assert build_schema(TOTAL) == build_schema(TOTAL, ())


def test_rows_become_cells_and_empty_rows_are_dropped(receipt_page):
    backend = FakeBackend(
        {
            "total": cell(9, 6),
            "items": [
                item("PC", 1, 9, 9),
                item(None, None, None, None),
                item("BAG", None, None, 0.5),
            ],
        }
    )
    result = LlmExtractor(backend).extract(receipt_page, TOTAL, [ITEMS])
    names = [f.name for f in result.fields]
    assert names[0] == "total"
    assert names[1:5] == [
        "items.1.name",
        "items.1.quantity",
        "items.1.unit_price",
        "items.1.amount",
    ]
    # 第 2 行全空，跳过；第 3 行变成 items.2
    assert names[5:] == ["items.2.name", "items.2.quantity", "items.2.unit_price", "items.2.amount"]
    by_name = {f.name: f for f in result.fields}
    assert by_name["items.1.quantity"].value == 1.0  # 整数也变成 float
    assert by_name["items.1.amount"].row == 5  # 每一格都用这一行的行号
    assert by_name["items.2.quantity"].value is None
    assert "商品明细" not in backend.messages[1].content  # 给模型看的是说明，不是中文标签
    assert "items（表格" in backend.messages[1].content
    assert "  - amount（数字）" in backend.messages[1].content


def test_missing_table_means_no_rows(receipt_page):
    result = LlmExtractor(FakeBackend({"total": cell(9, 6)})).extract(receipt_page, TOTAL, [ITEMS])
    assert [f.name for f in result.fields] == ["total"]


@pytest.mark.parametrize("bad", [{"rows": 1}, [1], [{"row": 1, "name": "A"}]])
def test_bad_table_answer_is_an_error(receipt_page, bad):
    backend = FakeBackend({"total": cell(9, 6), "items": bad})
    with pytest.raises(ExtractionError):
        LlmExtractor(backend).extract(receipt_page, TOTAL, [ITEMS])


def rows(*items: tuple[str, float | None, float | None, float]) -> list[F]:
    fields: list[F] = []
    for i, (name, quantity, price, amount) in enumerate(items, start=1):
        fields += [
            F(cell_name("items", i, "name"), "string", name),
            F(cell_name("items", i, "quantity"), "number", quantity),
            F(cell_name("items", i, "unit_price"), "number", price),
            F(cell_name("items", i, "amount"), "number", amount),
        ]
    return fields


def test_items_add_up_to_total():
    fields = [F("total", "number", 12.5), *rows(("A", 1.0, 10.0, 10.0), ("B", None, None, 2.5))]
    result = RuleValidator().validate(fields)
    assert result.issues == ()
    assert {"items_sum", "item_line"} <= set(result.passed)


def test_items_not_adding_up_flags_only_the_amounts():
    fields = [F("total", "number", 20.0), *rows(("A", 1.0, 10.0, 10.0), ("B", 1.0, 2.5, 2.5))]
    [issue] = RuleValidator().validate(fields).issues
    assert issue.rule == "items_sum" and issue.severity == "warning"
    assert issue.fields == ("items.1.amount", "items.2.amount")  # 不标总额
    assert "12.50" in issue.message and "总额是 20.00" in issue.message


def test_items_compare_with_subtotal_when_there_is_one():
    fields = [
        F("subtotal", "number", 10.0),
        F("total", "number", 10.6),  # 加了税
        *rows(("A", 2.0, 5.0, 10.0)),
    ]
    assert RuleValidator().validate(fields).issues == ()


def test_wrong_line_is_flagged_row_by_row():
    fields = [
        F("total", "number", 23.0),
        *rows(("A", 2.0, 5.0, 10.0), ("B", 3.0, 4.0, 13.0)),  # 3 × 4 = 12，不是 13
    ]
    [issue] = [i for i in RuleValidator().validate(fields).issues if i.rule == "item_line"]
    assert issue.message.startswith("第 2 行：")
    assert issue.fields == ("items.2.quantity", "items.2.unit_price", "items.2.amount")


def test_line_rule_is_skipped_without_quantities():
    fields = [F("total", "number", 2.5), *rows(("B", None, None, 2.5))]
    result = RuleValidator().validate(fields)
    assert "item_line" in result.skipped and "items_sum" in result.passed


def item_report(receipt_page) -> DocumentReport:
    fields = (F("total", "number", 9.0, 6), *rows(("PC", 1.0, 9.0, 9.0)))
    fields = tuple(
        F(f.name, f.type, f.value, 5) if f.name.startswith("items.") else f for f in fields
    )
    result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
    return DocumentReport(
        source="receipt.jpg",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator().validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 11),
    )


def test_cells_get_evidence_like_any_field(receipt_page):
    by_name = {a.field.name: a for a in item_report(receipt_page).aligned}
    amount = by_name["items.1.amount"]
    assert amount.status == "matched" and amount.evidence is not None
    assert amount.evidence.row == 5
    assert by_name["items.1.name"].status in ("matched", "fuzzy")


def test_cells_are_saved_and_read_back(factory, receipt_page, tmp_path):
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"not really a jpeg")
    with session_scope(factory) as session:
        document_id = save_document(session, path, [item_report(receipt_page)]).id
    with session_scope(factory) as session:
        document = session.get(Document, document_id)
        assert document is not None
        [loaded] = load_reports(document)
    names = [a.field.name for a in loaded.aligned]
    assert names == [
        "total",
        "items.1.name",
        "items.1.quantity",
        "items.1.unit_price",
        "items.1.amount",
    ]


def test_excel_has_an_items_sheet(receipt_page, tmp_path):
    path = tmp_path / "out.xlsx"
    ExcelExporter().export([item_report(receipt_page)], path)
    book = load_workbook(path)
    assert book.sheetnames == ["汇总", "明细", "商品明细", "来源"]

    summary = book["汇总"]
    assert [c.value for c in summary[1]] == ["文件", "total", "需要确认"]  # 格子不进汇总

    items = book["商品明细"]
    assert [c.value for c in items[1]] == [
        "文件",
        "第几行",
        "name",
        "quantity",
        "unit_price",
        "amount",
    ]
    assert [c.value for c in items[2]] == ["receipt.jpg", 1, "PC", 1, 9, 9]
    assert items["D2"].number_format == "0.###"
    assert items["F2"].number_format == "#,##0.00"


def test_no_items_sheet_without_tables(report, tmp_path):
    path = tmp_path / "out.xlsx"
    ExcelExporter().export([report], path)
    assert "商品明细" not in load_workbook(path).sheetnames


def test_table_spec_from_template():
    assert isinstance(ITEMS, TableSpec)
    assert ITEMS.name == "items"


def test_discount_line_passes_the_line_rule():
    """折扣行：3x 2.00，金额是 -6.00。"""
    fields = [
        F("total", "number", 41.97),
        *rows(("WATERMELON", 3.0, 15.99, 47.97), ("TPD", 3.0, 2.0, -6.0)),
    ]
    result = RuleValidator().validate(fields)
    assert [i.rule for i in result.issues] == []


def test_discount_is_found_however_it_is_written():
    from jacara.plugins.aligners.text_match import number_variants

    assert "6.00" in number_variants(-6.0, loose=False)


def test_cell_may_be_one_row_off(receipt_page):
    """一样商品常占两行，模型只给一个行号：格子的出处差一行不算问题；普通字段要同一行。"""
    fields = (F("items.1.name", "string", "Date:", 5), F("merchant", "string", "Date:", 5))
    result = ExtractionResult(fields, "{}", "m", "fake", "v", 1)
    cell, plain = TextMatchAligner().align(receipt_page, result)
    assert cell.evidence is not None and cell.evidence.row == 4
    assert not cell.needs_review
    assert plain.needs_review and "第 5 行" in plain.note


def test_discount_written_as_positive_is_flagged():
    """原文 "6.00-" 是折扣，模型给了正的 6.0：出处找得到，但要标黄说明正负号不对。"""
    from tests.conftest import make_line, make_page

    page = make_page(
        make_line("290957  3x  2.00", 10, 10, 200, 30), make_line("6.00-", 300, 10, 360, 30)
    )
    wrong = ExtractionResult((F("items.1.amount", "number", 6.0, 1),), "{}", "m", "fake", "v", 1)
    right = ExtractionResult((F("items.1.amount", "number", -6.0, 1),), "{}", "m", "fake", "v", 1)
    [flagged] = TextMatchAligner().align(page, wrong)
    [fine] = TextMatchAligner().align(page, right)
    assert flagged.needs_review and "负数" in flagged.note
    assert not fine.needs_review
