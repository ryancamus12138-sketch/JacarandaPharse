"""Standard template workbook: everything a template holds survives an Excel round trip, missing
optional columns take defaults, newer layouts are refused, and unrecognized columns are
reported."""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from jacara.workbook import standard

from .test_workbook import xlsx

ENGLISH = {"Accept-Language": "en"}
KEPT = (
    "name",
    "label",
    "type",
    "required",
    "extract",
    "export",
    "sensitive",
    "options",
    "aliases",
    "description",
    "compute",
    "unique",
    "names",
    "descriptions",
)


def upload(client, data: bytes, headers=None) -> dict:
    response = client.post(
        "/api/v1/templates/excel",
        files={"file": ("t.xlsx", data, standard_xlsx())},
        headers=headers or {},
    )
    assert response.status_code == 200, response.text
    return response.json()


def standard_xlsx() -> str:
    return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def rich(client) -> dict:
    """A copy of the hotel folio starter (formulas, uniqueness, 16 languages) with a date range
    field, custom rules, an English primary language and template texts in Chinese."""
    created = client.post(
        "/api/v1/templates",
        json={"source": "starter", "starter": "hotel_folio"},
        headers=ENGLISH,
    ).json()
    period = {
        "name": "period",
        "label": "Stay",
        "type": "date_range",
        "range_export": "columns",
        "range_separator": " to ",
        "names": {"zh": "入住期间"},
        "descriptions": {"zh": "入住和离店日期"},
        "description": "Arrival and departure dates",
    }
    body = {
        "title": created["title"],
        "titles": created["titles"],
        "language": created["language"],
        "description": created["description"],
        "descriptions": created["descriptions"],
        "fields": [*created["fields"], period],
        "tables": created["tables"],
        "rules": [r["id"] for r in created["rules"]],
        "custom_rules": [
            {"title": "Positive nights", "check": "nights > 0", "severity": "warning"},
            {
                "title": "Positive amounts",
                "check": "amount > 0",
                "severity": "error",
                "per_row": "items",
            },
        ],
        "domains": created["domains"],
        "countries": created["countries"],
    }
    saved = client.put(f"/api/v1/templates/{created['id']}", json=body)
    assert saved.status_code == 200, saved.text
    return saved.json()


def same(a: dict, b: dict) -> None:
    for left, right in zip(a["fields"], b["fields"], strict=True):
        assert {k: left.get(k) for k in KEPT} == {k: right.get(k) for k in KEPT}, left["name"]
        if left["type"] == "date_range":
            assert (left["range_export"], left["range_separator"]) == (
                right["range_export"],
                right["range_separator"],
            )
    for left, right in zip(a["tables"], b["tables"], strict=True):
        assert (left["name"], left["label"], left["names"], left["descriptions"]) == (
            right["name"],
            right["label"],
            right["names"],
            right["descriptions"],
        )
        for x, y in zip(left["columns"], right["columns"], strict=True):
            assert {k: x.get(k) for k in KEPT} == {k: y.get(k) for k in KEPT}
    assert (a["language"], a["titles"], a["descriptions"]) == (
        b["language"],
        b["titles"],
        b["descriptions"],
    )
    assert a["custom_rules"] == b["custom_rules"]
    assert [r["id"] for r in a["rules"]] == [r["id"] for r in b["rules"]]


@pytest.mark.parametrize("headers", [{}, ENGLISH])
def test_excel_round_trip_keeps_everything(client, rich, headers):
    exported = client.get(f"/api/v1/templates/{rich['id']}/export", headers=headers).content
    book = load_workbook(io.BytesIO(exported))
    fields = next(ws for ws in book.worksheets if ws.title in ("字段", "Fields"))
    header = [c.value for c in fields[1]]
    assert ("名称（zh）" if not headers else "Name (zh)") in header
    analysis = upload(client, exported)
    assert analysis["kind"] == "standard" and analysis["ignored"] == []
    imported = client.post("/api/v1/templates/import", json=analysis["template"]).json()
    same(rich, imported)
    period = next(f for f in imported["fields"] if f["name"] == "period")
    assert (period["range_export"], period["range_separator"]) == ("columns", " to ")
    nights = next(f for f in imported["fields"] if f["name"] == "nights")
    assert nights["compute"] == "check_out - check_in"


def test_missing_optional_columns_take_defaults(client, rich):
    """A workbook without the optional columns and rows (deleted by hand, or written by an earlier
    layout version) imports with their defaults."""
    book = load_workbook(io.BytesIO(client.get(f"/api/v1/templates/{rich['id']}/export").content))
    added = {"公式", "不能重复", "日期范围导出", "分隔符", "条件", "逐行检查的表格"}
    for ws in book.worksheets:
        if ws.title == "模板":
            drop = []
            for n in range(2, ws.max_row + 1):
                label = str(ws.cell(row=n, column=1).value or "")
                if label == "主语言" or "（" in label:
                    drop.append(n)
            for n in reversed(drop):
                ws.delete_rows(n)
            continue
        columns = [
            n
            for n in range(1, ws.max_column + 1)
            if (text := ws.cell(row=1, column=n).value) in added
            or (isinstance(text, str) and "（" in text)
        ]
        for n in reversed(columns):
            ws.delete_cols(n)
    analysis = upload(client, xlsx(book))
    assert analysis["ignored"] == []
    template = analysis["template"]
    assert template["language"] == "zh" and template["custom_rules"] == []
    nights = next(f for f in template["fields"] if f["name"] == "nights")
    assert (nights["compute"], nights["names"]) == ("", {})
    folio = next(f for f in template["fields"] if f["name"] == "folio_no")
    assert folio["unique"] is False
    period = next(f for f in template["fields"] if f["name"] == "period")
    assert (period["range_export"], period["range_separator"]) == ("normalized", " ~ ")


def test_newer_layout_is_refused(client, rich):
    book = load_workbook(io.BytesIO(client.get(f"/api/v1/templates/{rich['id']}/export").content))
    info = book["模板"]
    n = next(
        n for n in range(2, info.max_row + 1) if info.cell(row=n, column=1).value == "格式版本"
    )
    info.cell(row=n, column=2, value=standard.FORMAT_VERSION + 1)
    response = client.post(
        "/api/v1/templates/excel", files={"file": ("t.xlsx", xlsx(book), standard_xlsx())}
    )
    assert response.status_code == 422
    assert "更新版本" in response.json()["detail"]


def test_unrecognized_columns_are_reported_and_retired_ones_skipped(client, rich, monkeypatch):
    book = load_workbook(io.BytesIO(client.get(f"/api/v1/templates/{rich['id']}/export").content))
    fields = book["字段"]
    fields.cell(row=1, column=fields.max_column + 1, value="备注")
    fields.cell(row=1, column=fields.max_column + 1, value="旧列")
    book["模板"].append(["审批人", "张三"])
    monkeypatch.setattr(standard, "RETIRED", ("旧列",))
    analysis = upload(client, xlsx(book))
    assert analysis["ignored"] == [
        "「模板」表中无法识别的项目已忽略：审批人",
        "「字段」表中无法识别的列已忽略：备注",
    ]
    english = upload(client, xlsx(book), ENGLISH)
    assert english["ignored"][1] == 'Unrecognized columns in sheet "Fields" were ignored: 备注'
    imported = client.post("/api/v1/templates/import", json=analysis["template"]).json()
    same(rich, imported)
