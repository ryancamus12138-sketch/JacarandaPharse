"""Multilingual templates: field, table and template names in several languages, switchable on the
review page and in exports."""

from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from jacara.templates import TemplateError, from_definition, parse_template
from tests.test_template_library import detail, process, upload, values

from .conftest import raises

RAW = {
    "name": "demo",
    "version": 1,
    "title": "演示发票",
    "titles": {"en": "Demo invoice", "ja": "デモ請求書"},
    "fields": [
        {
            "name": "invoice_no",
            "label": "发票号码",
            "type": "string",
            "aliases": ["Inv No"],
            "names": {"en": "Invoice number", "ja": "請求書番号"},
        },
        {"name": "total", "label": "总额", "type": "number", "names": {"en": "Total"}},
    ],
    "tables": [
        {
            "name": "items",
            "label": "明细",
            "names": {"en": "Items"},
            "columns": [
                {"name": "amount", "label": "金额", "type": "number", "names": {"ja": "金額"}}
            ],
        }
    ],
}


def test_names_and_languages():
    template = parse_template(RAW, "demo")
    assert template.languages == ("zh", "en", "ja")
    assert template.title_in("ja") == "デモ請求書"
    assert template.title_in("ko") == "演示发票"  # Languages not defined fall back to Chinese.
    assert template.labels("en") == {
        "invoice_no": "Invoice number",
        "total": "Total",
        "items": "Items",
        "items.amount": "金额",
    }
    assert template.labels("ja")["items.amount"] == "金額"
    assert template.labels()["invoice_no"] == "发票号码"


def test_names_go_into_the_prompt_once():
    field = parse_template(RAW, "demo").fields[0]
    assert "Inv No、Invoice number、請求書番号" in field.spec.description


def test_prompt_only_has_english_and_local_names():
    """Generic templates give the model English names only; Japanese templates add Japanese
    names."""
    generic = parse_template(RAW, "demo").specs[0].description
    assert "Invoice number" in generic and "請求書番号" not in generic
    japan = parse_template({**RAW, "countries": ["JP"]}, "demo").specs[0].description
    assert "請求書番号" in japan


def test_names_round_trip_through_definition():
    template = parse_template(RAW, "demo")
    again = from_definition(template.definition(), "demo", 1, template.title)
    assert again.fields == template.fields and again.tables == template.tables
    assert again.titles == template.titles


@pytest.mark.parametrize(
    ("names", "message"),
    [
        ({"english": "Total"}, "语言代码"),
        ({"en": 3}, "应为文字"),
        ({"en": "x" * 201}, "过长"),
        (["en"], "需写作"),
    ],
)
def test_bad_names_say_why(names, message):
    raw = {**RAW, "fields": [{**RAW["fields"][1], "names": names}]}
    with raises(TemplateError, message):
        parse_template(raw, "demo")


def test_review_and_export_switch_language(client, factory, storage, receipt_page):
    created = client.post(
        "/api/v1/templates", json={"source": "starter", "starter": "receipt_general"}
    ).json()
    for f in created["fields"]:
        if f["name"] == "total":
            f["names"] = {"en": "Total amount", "ja": "合計金額"}
    saved = client.put(
        f"/api/v1/templates/{created['id']}",
        json={
            "title": "我的收据",
            "titles": {"en": "My receipt"},
            "category": "收据",
            "fields": created["fields"],
            "tables": created["tables"],
            "rules": [r["id"] for r in created["rules"]],
            "note": "加英文、日文名",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["titles"] == {"en": "My receipt"}
    assert body["languages"][0] == "zh" and {"en", "ja"} <= set(body["languages"])

    document_id = upload(client, created["id"])
    assert process(factory, storage, document_id, receipt_page) == "done"
    shown = detail(client, document_id)
    assert shown["extractions"][0]["languages"][0] == "zh"
    total = values(shown)["total"]
    assert total["label"] == "总额" and total["names"]["ja"] == "合計金額"
    assert shown["extractions"][0]["label_sets"]["en"]["total"] == "Total amount"

    def headers(lang: str | None) -> list[str]:
        query = f"?lang={lang}" if lang else ""
        data = client.get(f"/api/v1/documents/{document_id}/export.xlsx{query}").content
        return [str(c.value) for c in load_workbook(io.BytesIO(data))["汇总"][1]]

    assert "总额" in headers(None)
    assert "Total amount" in headers("en") and "总额" not in headers("en")
    assert "合計金額" in headers("ja")
    assert "总额" in headers("ko")  # Languages not defined fall back to Chinese.
    csv = client.get(f"/api/v1/documents/export.csv?ids={document_id}&lang=en")
    assert csv.status_code == 200 and "Total amount" in csv.content.decode("utf-8-sig")


# ---------- Every starter template switches between Chinese and English; China quality inspection
# report ----------


def test_every_starter_has_english_names():
    from jacara.templates import STARTERS, load_starter

    for name in STARTERS:
        t = load_starter(name)
        assert t.title_in("en") != t.title, name
        places = [*t.fields, *t.tables, *(c for tb in t.tables for c in tb.columns)]
        missing = [p.name for p in places if "en" not in dict(p.names)]
        assert missing == [], f"{name} 没有英文名：{missing}"


def test_cn_quality_report_rules():
    from jacara.plugins.validators.rules import REGIONS, RULES
    from jacara.templates import load_starter

    t = load_starter("cn_quality_report")
    assert t.countries == ("CN",) and t.foreign_rules() == []
    generic = {f.name for f in load_starter("quality_report").fields}
    # All generic fields present, plus those required in China.
    assert generic < {f.name for f in t.fields}
    assert REGIONS["cn_cma_no"] == "CN"
    cma = RULES["cn_cma_no"]
    assert cma.check({"cma_no": "2015 0012 3456"}) is None
    assert "12 位" in (cma.check({"cma_no": "20150O123456"}) or "")
    order = RULES["cn_sampling_dates"]
    assert order.check({"sampling_date": "2024-05-01", "issue_date": "2024-05-10"}) is None
    assert order.check({"sampling_date": "2024-05-11", "issue_date": "2024-05-10"})
