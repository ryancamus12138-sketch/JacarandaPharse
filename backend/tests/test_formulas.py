"""Computed fields: formula validation and evaluation, computation during processing, recomputation
after review edits."""

from __future__ import annotations

import re

import pytest

from jacara.core.i18n import render
from jacara.formulas import FormulaError, Inputs, Scope, evaluate, order, parse_formula
from jacara.pipeline.compute import compute
from jacara.templates import TemplateError, load_starter, parse_template

from .conftest import make_line, make_page, raises
from .test_template_library import SchemaBackend, detail, process, upload, values

SCOPE = Scope(
    fields={"check_in": "date", "check_out": "date", "total": "number", "hotel": "string"},
    tables={"items": {"quantity": "number", "unit_price": "number", "name": "string"}},
)
ROW = Scope(SCOPE.fields, SCOPE.tables, SCOPE.tables["items"])


def test_dates_subtract_to_days():
    formula = parse_formula("check_out - check_in", SCOPE)
    assert formula.fields == {"check_in", "check_out"}
    outcome = evaluate(formula, Inputs({"check_in": "2017-12-31", "check_out": "2018-01-02"}, {}))
    assert outcome.value == 2
    labels = {"check_in": "入住日期", "check_out": "离店日期"}
    assert render(formula.readable(labels)) == "离店日期 − 入住日期"


def test_missing_value_says_which():
    formula = parse_formula("check_out - check_in", SCOPE)
    outcome = evaluate(formula, Inputs({"check_in": None, "check_out": "2018-01-02"}, {}))
    assert outcome.value is None and outcome.missing == "check_in"


def test_row_formula_and_sum():
    row = parse_formula("round(quantity * unit_price, 2)", ROW)
    assert row.columns == {"quantity", "unit_price"}
    assert evaluate(row, Inputs({}, {}, {"quantity": "3", "unit_price": 1.105})).value == 3.31
    total = parse_formula("sum(items.unit_price) + count(items)", SCOPE)
    rows = [{"unit_price": "2.5"}, {"unit_price": 1}]
    assert evaluate(total, Inputs({}, {"items": rows})).value == 5.5


def test_divide_by_zero():
    formula = parse_formula("total / (total - total)", SCOPE)
    assert render(evaluate(formula, Inputs({"total": 3}, {})).problem) == "除以 0"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("hotel + 1", "文字字段"),
        ("check_in + 1", "日期只能与日期相减"),
        ("check_in", "须为数字"),
        ("nights * 2", "没有叫 nights 的字段"),
        ("__import__('os')", "不支持函数 __import__()"),
        ("total.real", "sum("),
        ("sum(items.name)", "不是数字"),
        ("total if total else 0", "只能使用"),
        ("1 +", "公式有误"),
    ],
)
def test_bad_formulas_say_why(text, message):
    with raises(FormulaError, re.escape(message)):
        parse_formula(text, SCOPE)


def test_formulas_in_a_loop_are_refused():
    a = parse_formula("total + 1", Scope({"total": "number", "a": "number"}, {}))
    b = parse_formula("a + 1", Scope({"total": "number", "a": "number"}, {}))
    # total uses a and a uses total.
    with raises(FormulaError, "循环引用"):
        order({"a": a, "total": b})


def test_template_checks_formulas():
    raw = {
        "name": "t",
        "version": 1,
        "fields": [
            {"name": "check_in", "type": "date"},
            {"name": "nights", "label": "晚数", "type": "number", "compute": "check_out - x"},
        ],
    }
    with raises(TemplateError, "「晚数」的公式：没有叫 check_out 的字段"):
        parse_template(raw, "t")
    raw["fields"][1] |= {"type": "string", "compute": "1"}
    with raises(TemplateError, "type 需为 number"):
        parse_template(raw, "t")


def test_hotel_nights_are_computed_not_asked():
    hotel = load_starter("hotel_folio")
    assert "nights" not in {s.name for s in hotel.specs}
    [done] = compute(hotel, {"check_in": "2017-12-31", "check_out": "2018-01-01"})
    assert (done.name, done.value) == ("nights", 1)
    assert render(done.note) == "按公式计算：离店日期 − 入住日期 = 1"


# ---------- Processing and review ----------

HOTEL = {
    "hotel": ("OYO Hotel Nanganallur", 1),
    "guest": ("Sanjay", 2),
    "check_in": ("2017-12-31", 3),
    "check_out": ("2018-01-02", 4),
    "total": (1939, 5),
}


@pytest.fixture
def hotel_page():
    return make_page(
        make_line("OYO Hotel Nanganallur", 10, 10, 300, 30),
        make_line("Guest Name: Sanjay", 10, 40, 300, 60),
        make_line("Check In 2017-12-31", 10, 70, 300, 90),
        make_line("Check Out 2018-01-02", 10, 100, 300, 120),
        make_line("Grand Total Rs 1939", 10, 130, 300, 150),
    )


@pytest.fixture
def hotel_template(client):
    created = client.post("/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"})
    assert created.status_code == 201, created.text
    nights = next(f for f in created.json()["fields"] if f["name"] == "nights")
    assert nights["compute"] == "check_out - check_in"
    return created.json()["id"]


def revise(client, value_id, **body):
    response = client.post(f"/api/v1/values/{value_id}/revisions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_nights_follow_the_dates(client, factory, storage, hotel_page, hotel_template):
    document_id = upload(client, hotel_template)
    assert process(factory, storage, document_id, hotel_page, SchemaBackend(HOTEL)) == "done"
    found = values(detail(client, document_id))
    nights = found["nights"]
    assert nights["origin"] == "computed" and nights["status"] == "computed"
    assert nights["current_value"] == "2.0" and nights["evidence_state"] == "verified"
    assert not nights["pending_review"]

    # Check-out date changed: nights recomputed, a compute revision recorded, not counted as
    # reviewed.
    revise(client, found["check_out"]["id"], action="edit", value="2018-01-04", reason="看错了")
    nights = values(detail(client, document_id))["nights"]
    assert nights["current_value"] == "4.0" and not nights["reviewed"]
    history = client.get(f"/api/v1/values/{nights['id']}/revisions").json()
    assert [r["action"] for r in history["revisions"]] == ["compute"]

    # The reviewer edited nights directly: later date changes no longer recompute it.
    revise(client, nights["id"], action="edit", value="3", reason="实际住了 3 晚")
    revise(client, found["check_in"]["id"], action="edit", value="2017-12-30", reason="看错了")
    assert values(detail(client, document_id))["nights"]["current_value"] == "3.0"


# ---------- Custom rules, regional rules ----------


def custom_template(*rules):
    raw = {
        "name": "t",
        "version": 1,
        "fields": [
            {"name": "total", "label": "总额", "type": "number"},
            {"name": "check_in", "label": "入住日期", "type": "date"},
            {"name": "check_out", "label": "离店日期", "type": "date"},
        ],
        "tables": [
            {
                "name": "items",
                "label": "明细",
                "columns": [
                    {"name": "quantity", "label": "数量", "type": "number"},
                    {"name": "unit_price", "label": "单价", "type": "number"},
                    {"name": "amount", "label": "金额", "type": "number"},
                ],
            }
        ],
        "custom_rules": list(rules),
    }
    return parse_template(raw, "t")


def test_custom_rules_check_values():
    from jacara.plugins.extractors import ExtractedField as F

    template = custom_template(
        {"title": "明细合计等于总额", "check": "sum(items.amount) == total"},
        {"check": "check_out > check_in", "severity": "warning"},
        {"title": "每行金额", "check": "quantity * unit_price == amount", "per_row": "items"},
    )
    fields = [
        F("total", "number", 10.0),
        F("check_in", "date", "2018-01-02"),
        F("check_out", "date", "2018-01-01"),
        F("items.1.quantity", "number", 2.0),
        F("items.1.unit_price", "number", 3.0),
        F("items.1.amount", "number", 6.0),
        F("items.2.quantity", "number", 1.0),
        F("items.2.unit_price", "number", 3.0),
        F("items.2.amount", "number", 3.0),
    ]
    result = template.validator().validate(fields)
    messages = {i.rule: i for i in result.issues}
    assert (
        render(messages["custom_1"].message)
        == "明细的金额合计 为 9，总额 为 10，不满足 明细的金额合计 = 总额"
    )
    assert render(messages["custom_2"].title) == "离店日期 > 入住日期"
    assert messages["custom_2"].severity == "warning"
    assert "custom_3" in result.passed
    assert template.definition()["custom_rules"][2]["per_row"] == "items"


def test_bad_custom_rule_says_which():
    with raises(TemplateError, "自定义规则「坏的」：没有叫 nights 的字段"):
        custom_template({"title": "坏的", "check": "nights == 1"})
    with raises(TemplateError, "比较符"):
        custom_template({"check": "total + 1"})
    with raises(TemplateError, "同为日期"):
        custom_template({"check": "check_in == total"})


def test_custom_rules_round_trip_through_editor(client):
    created = client.post("/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"})
    body = created.json()
    saved = client.put(
        f"/api/v1/templates/{body['id']}",
        json={
            "title": body["title"],
            "fields": body["fields"],
            "tables": body["tables"],
            "rules": [r["id"] for r in body["rules"]],
            "custom_rules": [{"title": "总额不超过 5000", "check": "total <= 5000"}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["custom_rules"][0]["check"] == "total <= 5000"
    bad = client.put(
        f"/api/v1/templates/{body['id']}",
        json={
            "title": body["title"],
            "fields": body["fields"],
            "custom_rules": [{"check": "totl"}],
        },
    )
    assert bad.status_code == 422 and "比较符" in bad.json()["detail"]


def test_rules_say_their_region(client):
    rules = {r["id"]: r["country"] for r in client.get("/api/v1/templates/rules").json()}
    assert rules["ato_evidence"] == "AU" and rules["cn_words"] == "CN"
    assert rules["stay_order"] == "" and rules["items_sum"] == ""
    hotel = load_starter("hotel_folio")
    assert all(rules[r.id] == "" for r in hotel.rules)


def test_old_copy_of_starter_is_offered_the_new_formula(client):
    body = client.post(
        "/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"}
    ).json()
    assert body["starter_formulas"] == {}
    for f in body["fields"]:
        if f["name"] == "nights":
            f["compute"] = ""  # As if copied before the starter template gained a formula.
    saved = client.put(
        f"/api/v1/templates/{body['id']}",
        json={"title": body["title"], "fields": body["fields"], "tables": body["tables"]},
    )
    assert saved.json()["starter_formulas"] == {"nights": "check_out - check_in"}


def test_formula_check_while_typing(client):
    fields = [
        {"name": "check_in", "label": "入住日期", "type": "date"},
        {"name": "check_out", "label": "离店日期", "type": "date"},
    ]
    tables = [
        {
            "name": "items",
            "label": "明细",
            "columns": [{"name": "amount", "label": "金额", "type": "number"}],
        }
    ]
    ok = client.post(
        "/api/v1/templates/formula-check",
        json={"text": "check_out - check_in", "fields": fields, "tables": tables},
    ).json()
    assert ok == {"ok": True, "readable": "离店日期 − 入住日期", "message": ""}
    bad = client.post(
        "/api/v1/templates/formula-check",
        json={"text": "amount > 0", "kind": "condition", "fields": fields, "tables": tables},
    ).json()
    assert not bad["ok"] and "没有叫 amount 的字段" in bad["message"]
    row = client.post(
        "/api/v1/templates/formula-check",
        json={
            "text": "amount > 0",
            "kind": "condition",
            "row": "items",
            "fields": fields,
            "tables": tables,
        },
    ).json()
    assert row["readable"] == "金额 > 0"


def test_imported_regional_template_keeps_its_region(client):
    """A China fully digital e-invoice template exported before regions existed (no region in the
    file) is recognized as a China template by its rules on import, without error."""
    exported = client.get("/api/v1/templates/starters").json()
    cn = next(t for t in exported if t["name"] == "cn_einvoice")
    body = {
        "title": cn["title"],
        "category": cn["category"],
        "fields": cn["fields"],
        "tables": cn["tables"],
        "rules": [r["id"] for r in cn["rules"]],
    }
    response = client.post("/api/v1/templates/import", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["countries"] == ["CN"]
    assert response.json()["domains"] == ["发票"]
