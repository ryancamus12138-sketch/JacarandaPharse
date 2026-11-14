"""Date ranges: the model copies the source text and code splits it into start and end;
verification, formulas, rules, export."""

from __future__ import annotations

import io
from datetime import date, datetime

import pytest
from openpyxl import load_workbook

from jacara.core.daterange import day_first_for, parse_date_range
from jacara.core.i18n import render
from jacara.formulas import FormulaError, Inputs, Scope, evaluate, parse_formula
from jacara.templates import parse_template

from .conftest import raises
from .test_template_library import SchemaBackend, detail, process, upload


@pytest.mark.parametrize(
    ("text", "day_first", "start", "end"),
    [
        ("July 1 - July 31 , 2014", None, "2014-07-01", "2014-07-31"),
        (
            "This invoice is for the billing period July 1 - July 31 , 2014",
            None,
            "2014-07-01",
            "2014-07-31",
        ),
        ("July 1-31, 2014", None, "2014-07-01", "2014-07-31"),
        ("Dec 28 - Jan 3, 2015", None, "2014-12-28", "2015-01-03"),
        ("2014年7月1日至7月31日", None, "2014-07-01", "2014-07-31"),
        ("2014.07.01-2014.07.31", None, "2014-07-01", "2014-07-31"),
        ("01/07/2014 ~ 31/07/2014", None, "2014-07-01", "2014-07-31"),
        ("07/01/2014 - 07/31/2014", None, "2014-07-01", "2014-07-31"),
        ("01/07/2014 - 02/07/2014", False, "2014-01-07", "2014-02-07"),
        ("01/07/2014 - 02/07/2014", True, "2014-07-01", "2014-07-02"),
        ("1st July to 31st July 2014", None, "2014-07-01", "2014-07-31"),
    ],
)
def test_parse_ranges(text, day_first, start, end):
    parsed = parse_date_range(text, day_first)
    assert parsed.problem is None and parsed.range is not None
    assert (parsed.range.start, parsed.range.end) == (
        date.fromisoformat(start),
        date.fromisoformat(end),
    )


def test_parse_problems():
    unsure = parse_date_range("01/07/2014 - 02/07/2014")
    assert unsure.range is not None and "无法区分" in (unsure.problem or "")
    assert parse_date_range("7月1日至31日").problem == "原文中没有年份"
    assert parse_date_range("hello").range is None
    backwards = parse_date_range("2014-07-31 ~ 2014-07-01")
    assert backwards.problem == "结束日期早于开始日期"
    single = parse_date_range("截至 2014-07-31")
    assert single.range is not None and single.range.start is None
    found = parse_date_range("July 1 - July 31 , 2014").range
    assert (
        found is not None and found.days == 31 and found.text(" 至 ") == "2014-07-01 至 2014-07-31"
    )
    assert day_first_for(["US"]) is False and day_first_for(["CN", "AU"]) is True
    assert day_first_for([]) is None and day_first_for(["US", "AU"]) is None


def test_formulas_use_start_end_days():
    scope = Scope({"period": "date_range", "invoice_date": "date"}, {})
    days = parse_formula("period.days", scope)
    assert evaluate(days, Inputs({"period": "July 1 - July 31 , 2014"}, {})).value == 31
    gap = parse_formula("invoice_date - period.end", scope)
    inputs = Inputs({"period": "July 1 - July 31 , 2014", "invoice_date": "2014-08-03"}, {})
    assert evaluate(gap, inputs).value == 3
    assert (
        render(gap.readable({"period": "账期", "invoice_date": "发票日期"}))
        == "发票日期 − 账期的结束"
    )
    with raises(FormulaError, r"period\.start"):
        parse_formula("period + 1", scope)
    with raises(FormulaError, "只能使用"):
        parse_formula("period.month", scope)


BILL = {
    "account": ("296664039561", None),
    "period": ("July 1 - July 31 , 2014", None),
    "total": (4.11, None),
}


def bill_template(export="normalized"):
    return {
        "title": "云服务账单",
        "fields": [
            {"name": "account", "label": "账号", "type": "string"},
            {"name": "period", "label": "账期", "type": "date_range", "range_export": export},
            {"name": "total", "label": "总额", "type": "number"},
            {"name": "days", "label": "天数", "type": "number", "compute": "period.days"},
        ],
        "tables": [],
        "custom_rules": [{"title": "账期在今天之前", "check": "period.end > period.start"}],
    }


def bill_page():
    from .conftest import make_line, make_page

    return make_page(
        make_line("Account number: 296664039561", 10, 10, 300, 30),
        make_line(
            "This invoice is for the billing period July 1 - July 31 , 2014", 10, 40, 480, 60
        ),
        make_line("TOTAL $4.11", 10, 70, 200, 90),
    )


def test_template_with_date_range_checks():
    template = parse_template({"name": "bill", "version": 1, **bill_template()}, "bill")
    assert template.ranges()["period"].range_export == "normalized"
    assert "照抄原文" in next(s for s in template.specs if s.name == "period").description
    from jacara.plugins.extractors.base import ExtractedField

    bad = template.validator().validate(
        [ExtractedField("period", "date_range", "2014-07-31 ~ 2014-07-01")]
    )
    assert any(i.rule == "range_order_period" and i.severity == "error" for i in bad.issues)
    unsure = template.validator().validate(
        [ExtractedField("period", "date_range", "01/07/2014 - 02/07/2014")]
    )
    assert any(i.rule == "range_period" and i.severity == "warning" for i in unsure.issues)


@pytest.mark.parametrize(
    ("export", "headers", "cells"),
    [
        ("normalized", ["账期"], ["2014-07-01 ~ 2014-07-31"]),
        ("original", ["账期"], ["July 1 - July 31 , 2014"]),
        ("columns", ["账期 开始", "账期 结束"], [date(2014, 7, 1), date(2014, 7, 31)]),
    ],
)
def test_bill_with_date_range_end_to_end(client, factory, storage, export, headers, cells):
    created = client.post(
        "/api/v1/templates", json={"source": "blank", "content": bill_template(export)}
    )
    assert created.status_code == 201, created.text
    document_id = upload(client, created.json()["id"])
    assert process(factory, storage, document_id, bill_page(), SchemaBackend(BILL)) == "done"

    [extraction] = detail(client, document_id)["extractions"]
    values = {v["name"]: v for v in extraction["values"]}
    period = values["period"]
    assert period["status"] == "matched" and period["current_value"] == "2014-07-01 ~ 2014-07-31"
    assert period["evidence"]["quote"] == "July 1 - July 31 , 2014"
    assert period["range"] == {
        "start": "2014-07-01",
        "end": "2014-07-31",
        "days": 31,
        "problem": None,
    }
    assert values["days"]["current_value"] == "31.0"

    exported = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    assert exported.status_code == 200
    sheet = load_workbook(io.BytesIO(exported.content))["汇总"]
    row1 = [c.value for c in sheet[1]]
    row2 = [c.value for c in sheet[2]]
    first = row1.index(headers[0])
    assert row1[first : first + len(headers)] == headers
    got = row2[first : first + len(headers)]
    got = [g.date() if isinstance(g, datetime) else g for g in got]
    assert got == cells
    comment = sheet.cell(row=2, column=first + 1).comment
    assert comment is not None and "July 1 - July 31" in comment.text


def test_export_csv(client, factory, storage):
    import csv as csvlib
    import zipfile

    created = client.post(
        "/api/v1/templates", json={"source": "blank", "content": bill_template("columns")}
    )
    template_id = created.json()["id"]
    document_id = upload(client, template_id)
    assert process(factory, storage, document_id, bill_page(), SchemaBackend(BILL)) == "done"

    single = client.get("/api/v1/documents/export.csv", params={"ids": [document_id]})
    assert single.status_code == 200 and single.headers["content-type"].startswith("text/csv")
    text = single.content.decode("utf-8-sig")
    rows = list(csvlib.reader(io.StringIO(text)))
    header, row = rows[0], rows[1]
    assert row[header.index("账期 开始")] == "2014-07-01"
    assert row[header.index("总额")] == "4.11"
    assert "来源" not in text

    # Additional sheets (here the key-value sheet of key-value discovery): one .csv per sheet,
    # packed into a zip.
    other = upload(client, "discover")
    pairs = [{"key": "Account", "value": "296664039561", "type": "string", "blocks": []}]
    assert process(factory, storage, other, bill_page(), SchemaBackend(pairs=pairs)) == "done"
    packed = client.get("/api/v1/documents/export.csv", params={"ids": [document_id, other]})
    names = zipfile.ZipFile(io.BytesIO(packed.content)).namelist()
    assert names == ["汇总.csv", "键值对.csv"]
    formats = [e["format"] for e in client.get("/api/v1/exports").json()["items"]]
    assert "csv" in formats and "csv-zip" in formats
