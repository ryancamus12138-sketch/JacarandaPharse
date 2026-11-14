"""Template library: starter templates, my templates and versions, processing by template,
reprocessing, creating templates from samples, trial runs."""

from __future__ import annotations

import io
import json
import uuid

import pytest
from openpyxl import load_workbook

from jacara.core.cnmoney import parse_chinese_amount
from jacara.core.db import session_scope
from jacara.models import Document
from jacara.modules.templates.router import get_trial_pipeline
from jacara.pipeline.process import process_document
from jacara.pipeline.run import Pipeline
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import LlmExtractor
from jacara.plugins.model_backends import ChatResult
from jacara.plugins.validators.rules import RULES, RuleValidator, abn_valid
from jacara.templates import STARTERS, from_definition, load_starter

# ---------- Starter templates and rules ----------


@pytest.mark.parametrize("name", STARTERS)
def test_starter_loads_and_round_trips(name):
    template = load_starter(name)
    assert template.category and template.title
    again = from_definition(template.definition(), name, 1, template.title)
    assert again.fields == template.fields
    assert again.tables == template.tables
    assert again.rules == template.rules


def test_sensitive_fields_are_not_exported_by_default():
    rail = load_starter("cn_rail_ticket")
    passenger_id = next(f for f in rail.fields if f.name == "passenger_id")
    assert passenger_id.sensitive and not passenger_id.export


def test_aliases_go_into_the_prompt():
    hotel = load_starter("hotel_folio")
    check_in = next(f for f in hotel.fields if f.name == "check_in")
    assert "Arrival" in check_in.spec.description


@pytest.mark.parametrize(
    ("words", "amount"),
    [
        ("壹佰贰拾叁圆肆角伍分", 123.45),
        ("⊗壹仟零伍元整", 1005.0),
        ("拾圆整", 10.0),
        ("贰万叁仟肆佰元", 23400.0),
        ("叁佰陆拾元零伍分", 360.05),
        ("壹亿贰仟万圆整", 120_000_000.0),
        ("看不懂", None),
    ],
)
def test_chinese_amount(words, amount):
    assert parse_chinese_amount(words) == amount


def test_abn_checksum():
    assert abn_valid("51 824 753 556") is True  # Example ABN from ATO documentation.
    assert abn_valid("51 824 753 557") is False
    assert abn_valid("GB 123 4567 89") is None  # Not an ABN; not checked.


def validate(rule_ids, **values):
    return RuleValidator([RULES[r] for r in rule_ids]).validate(
        [F(k, "number" if isinstance(v, float) else "string", v) for k, v in values.items()]
    )


def test_cn_invoice_rules():
    ok = validate(
        ["cn_tax_balance", "cn_words", "cn_invoice_no"],
        invoice_no="12345678901234567890",
        amount_total=100.0,
        tax_total=6.0,
        grand_total=106.0,
        grand_total_words="壹佰零陆圆整",
    )
    assert ok.issues == ()
    bad = validate(
        ["cn_tax_balance", "cn_words"],
        amount_total=100.0,
        tax_total=6.0,
        grand_total=160.0,
        grand_total_words="壹佰零陆圆整",
    )
    assert {i.rule for i in bad.issues} == {"cn_tax_balance", "cn_words"}


def test_rail_ticket_amounts():
    """Railway e-tickets issued to organizations: amount (excluding tax) + tax = fare; tickets
    issued to individuals have neither and are skipped."""
    ok = validate(["fare_tax_balance"], amount_total=394.04, tax_total=35.46, total=429.5)
    assert ok.passed == ("fare_tax_balance",)
    personal = validate(["fare_tax_balance"], total=9.0)
    assert personal.skipped == ("fare_tax_balance",)
    rail = load_starter("cn_rail_ticket")
    assert [f.name for f in rail.fields][:3] == ["invoice_no", "issue_date", "origin"]
    assert {"ticket_type", "amount_total", "tax_rate", "tax_total"} <= {f.name for f in rail.fields}


def test_air_itinerary_amounts():
    """Itinerary: fare + fuel + civil aviation development fund + other taxes = total; VAT is
    computed from fare and fuel per Announcement No. 39."""
    result = validate(
        ["air_total", "air_vat"],
        fare=1000.0,
        fuel_surcharge=90.0,
        caac_fund=50.0,
        total=1140.0,
        vat_rate="9%",
        vat=90.0,
    )
    assert result.issues == ()
    wrong = validate(["air_vat"], fare=1000.0, fuel_surcharge=90.0, vat_rate="9%", vat=98.1)
    assert [i.rule for i in wrong.issues] == ["air_vat"]
    air = load_starter("cn_air_itinerary")
    assert air.tables[0].name == "segments" and air.tables[0].required
    assert {"restriction", "gp_no", "insurance", "check_code"} <= {f.name for f in air.fields}


def test_receipt_rules():
    result = validate(
        ["receipt_balance", "ato_gst_credit", "gst_eleventh"],
        subtotal=100.0,
        surcharge=1.5,
        total=101.5,
        gst=12.0,
    )
    assert {i.rule for i in result.issues} == {"ato_gst_credit", "gst_eleventh"}
    assert "receipt_balance" in result.passed


def test_required_fields_are_errors():
    validator = RuleValidator([], required=[("total", "总额"), ("items", "商品明细")])
    result = validator.validate([F("total", "number", None)])
    assert [(i.rule, i.fields, i.severity) for i in result.issues] == [
        ("required", ("total",), "error"),
        ("required", ("items",), "error"),
    ]
    filled = validator.validate([F("total", "number", 9.0), F("items.1.amount", "number", 9.0)])
    assert filled.issues == () and "required" in filled.passed


# ---------- My templates: API and processing ----------


class SchemaBackend:
    """Fake model answering according to the JSON Schema in the request. values maps field ID →
    (value, line number); pairs are key-value pairs."""

    name = "fake"

    def __init__(self, values=None, pairs=None) -> None:
        self.values = values or {}
        self.pairs = pairs or []

    def chat(self, messages, *, json_schema=None):
        assert json_schema is not None
        properties = json_schema["properties"]
        if "pairs" in properties:
            answer = {"pairs": self.pairs}
        else:
            answer = {}
            for key, spec in properties.items():
                if spec.get("type") == "array":
                    answer[key] = []
                else:
                    value, row = self.values.get(key, (None, None))
                    answer[key] = {"value": value, "row": row}
        return ChatResult(json.dumps(answer), "fake-model", self.name, 0, 0, 1)


RECEIPT = {
    "merchant": ("BOOK TA _K (TAMAN DAYA) SDN BHD", 1),
    "date": ("2018-12-25", 4),
    "total": (9.0, 6),
}


class FakeOcr:
    name = "fake-ocr"

    def __init__(self, page) -> None:
        self.page = page

    def recognize(self, image):
        return self.page


def pipeline(page, backend):
    return Pipeline(ocr=FakeOcr(page), extractor=LlmExtractor(backend))


def process(factory, storage, document_id, page, backend=None):
    return process_document(
        uuid.UUID(str(document_id)),
        storage=storage,
        pipeline=pipeline(page, backend or SchemaBackend(RECEIPT)),
        factory=factory,
    )


def upload(client, template=None):
    data = {"template": template} if template else {}
    response = client.post(
        "/api/v1/documents", files={"file": ("r.jpg", b"x", "image/jpeg")}, data=data
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def detail(client, document_id, run=None):
    query = f"?run={run}" if run else ""
    return client.get(f"/api/v1/documents/{document_id}{query}").json()


def values(body):
    return {v["name"]: v for e in body["extractions"] for v in e["values"]}


@pytest.fixture
def mine(client):
    """Copy the "generic receipt" starter template and save a new version: total renamed, merchant
    tax id not exported."""
    created = client.post(
        "/api/v1/templates", json={"source": "starter", "starter": "receipt_general"}
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "mine" and body["version"] == 1 and body["category"] == "收据"
    for f in body["fields"]:
        if f["name"] == "total":
            f["label"] = "合计"
        if f["name"] == "merchant_tax_id":
            f["export"] = False
    saved = client.put(
        f"/api/v1/templates/{body['id']}",
        json={
            "title": "我的收据",
            "category": "收据",
            "fields": body["fields"],
            "tables": body["tables"],
            "rules": [r["id"] for r in body["rules"]],
            "note": "总额改名",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    return saved.json()["id"]


def test_template_list_versions_and_archive(client, mine):
    listed = client.get("/api/v1/templates").json()
    assert listed[0]["id"] == mine and listed[0]["title"] == "我的收据"
    # Built-in templates are not listed in the web UI; use ?builtin=true.
    assert all(t["kind"] == "mine" for t in listed)
    everything = client.get("/api/v1/templates?builtin=true").json()
    assert any(t["kind"] == "builtin" and t["name"] == "receipt" for t in everything)
    assert len(client.get("/api/v1/templates/starters").json()) == len(STARTERS)

    one = client.get(f"/api/v1/templates/{mine}").json()
    assert [v["version"] for v in one["versions"]] == [2, 1]
    old = client.get(f"/api/v1/templates/{mine}/versions/1").json()
    assert next(f for f in old["fields"] if f["name"] == "total")["label"] == "总额"

    client.post(f"/api/v1/templates/{mine}/archive", json={"archived": True})
    refused = client.post(
        "/api/v1/documents", files={"file": ("r.jpg", b"x", "image/jpeg")}, data={"template": mine}
    )
    assert refused.status_code == 422


def test_bad_template_is_refused_with_reason(client, mine):
    body = client.get(f"/api/v1/templates/{mine}").json()
    response = client.put(
        f"/api/v1/templates/{mine}",
        json={"title": "x", "fields": body["fields"], "rules": ["no_such_rule"]},
    )
    assert response.status_code == 422
    assert "no_such_rule" in response.json()["detail"]


def test_upload_with_my_template(client, factory, storage, receipt_page, mine):
    document_id = upload(client, mine)
    assert process(factory, storage, document_id, receipt_page) == "done"

    body = detail(client, document_id)
    [extraction] = body["extractions"]
    assert extraction["template"] == mine
    assert extraction["template_version"] == 2
    assert extraction["template_title"] == "我的收据"
    assert values(body)["total"]["label"] == "合计"
    assert "required" in extraction["current_checks"]["passed"]

    book = load_workbook(
        io.BytesIO(client.get(f"/api/v1/documents/{document_id}/export.xlsx").content)
    )
    headers = [c.value for c in book["汇总"][1]]
    assert "合计" in headers and "商家" in headers
    assert "商家税号" not in headers  # Set not to export.

    # The next upload without a template uses the previous one.
    second = upload(client)
    with session_scope(factory) as session:
        assert session.get_one(Document, uuid.UUID(second)).template_version_id is not None


def test_template_change_does_not_change_old_documents(
    client, factory, storage, receipt_page, mine
):
    document_id = upload(client, mine)
    process(factory, storage, document_id, receipt_page)
    body = client.get(f"/api/v1/templates/{mine}").json()
    for f in body["fields"]:
        if f["name"] == "total":
            f["label"] = "应付"
    client.put(
        f"/api/v1/templates/{mine}",
        json={"title": "我的收据", "fields": body["fields"], "rules": []},
    )
    assert values(detail(client, document_id))["total"]["label"] == "合计"  # Still version 2.


def test_reprocess_keeps_old_run_and_suggests(client, factory, storage, receipt_page, mine):
    document_id = upload(client, mine)
    process(factory, storage, document_id, receipt_page)
    total = values(detail(client, document_id))["total"]
    client.post(f"/api/v1/values/{total['id']}/revisions", json={"action": "confirm"})

    response = client.post(
        f"/api/v1/documents/{document_id}/reprocess", json={"reason": "template_changed"}
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "uploaded"
    # Not finished yet: still shows run 1.
    assert detail(client, document_id)["viewing_run"] == 1
    assert process(factory, storage, document_id, receipt_page) == "done"

    body = detail(client, document_id)
    assert body["current_run"] == 2 and body["viewing_run"] == 2
    assert [r["run"] for r in body["runs"]] == [2, 1]
    assert body["runs"][0]["reason"] == "模板已修改"
    assert values(body)["total"]["suggestion"] == {"action": "confirm", "value": "9.0", "run": 1}
    assert body["suggestions"] == 1

    adopted = client.post(f"/api/v1/documents/{document_id}/adopt-previous").json()
    assert adopted == {"confirmed": 1, "edited": 0}
    assert values(detail(client, document_id))["total"]["reviewed"] is True

    old = detail(client, document_id, run=1)
    assert old["viewing_run"] == 1
    old_total = values(old)["total"]
    refused = client.post(f"/api/v1/values/{old_total['id']}/revisions", json={"action": "confirm"})
    assert refused.status_code == 422  # Earlier runs are read-only.


def test_reprocess_other_needs_note(client, factory, storage, receipt_page, mine):
    document_id = upload(client, mine)
    process(factory, storage, document_id, receipt_page)
    response = client.post(f"/api/v1/documents/{document_id}/reprocess", json={"reason": "other"})
    assert response.status_code == 422


# ---------- Creating templates from samples ----------

PAIRS = [
    {"key": "商家名称", "value": "BOOK TA _K (TAMAN DAYA) SDN BHD", "type": "string", "row": 1},
    {"key": "Date", "value": "2018-12-25", "type": "date", "row": 4},
    {"key": "Total", "value": 9.0, "type": "number", "row": 6},
    {"key": "Total", "value": 9.0, "type": "number", "row": 6},  # Duplicates count once.
]


def test_discover_then_save_as_template(client, factory, storage, receipt_page):
    document_id = upload(client, "discover")
    backend = SchemaBackend(pairs=PAIRS)
    assert process(factory, storage, document_id, receipt_page, backend) == "done"

    body = detail(client, document_id)
    [extraction] = body["extractions"]
    assert extraction["template"] == "discover"
    assert extraction["template_title"] == "抽取所有键值对"
    found = values(body)
    assert [found[n]["label"] for n in ("kv.1", "kv.2", "kv.3")] == ["商家名称", "Date", "Total"]
    assert "kv.4" not in found
    assert found["kv.3"]["value"] == "9.0" and found["kv.3"]["evidence_state"] == "verified"

    book = load_workbook(
        io.BytesIO(client.get(f"/api/v1/documents/{document_id}/export.xlsx").content)
    )
    assert "键值对" in book.sheetnames

    response = client.post(
        "/api/v1/templates/from-sample",
        json={
            "document_id": document_id,
            "title": "书店收据",
            "category": "收据",
            "fields": [
                {"source": "kv.1", "name": "merchant", "label": "商家", "type": "string"},
                {
                    "source": "kv.3",
                    "name": "total",
                    "label": "总额",
                    "type": "number",
                    "required": True,
                },
                {"name": "note", "label": "备注"},
            ],
            "rules": ["total_positive"],
        },
    )
    assert response.status_code == 201, response.text
    template = response.json()["template"]
    total = next(f for f in template["fields"] if f["name"] == "total")
    assert total["aliases"] == ["Total"]

    after = detail(client, document_id)
    assert after["current_run"] == 2
    fields = values(after)
    assert set(fields) == {"merchant", "total", "note"}
    assert fields["total"]["value"] == "9.0" and fields["total"]["evidence"] is not None
    assert fields["note"]["value"] is None
    assert after["extractions"][0]["template"] == template["id"]


def test_trial_does_not_save(client, factory, storage, receipt_page, mine):
    document_id = upload(client, mine)
    process(factory, storage, document_id, receipt_page)
    fake = pipeline(receipt_page, SchemaBackend({"total": (9.0, 6)}))
    client.app.dependency_overrides[get_trial_pipeline] = lambda: lambda: fake

    response = client.post(f"/api/v1/templates/{mine}/trial", json={"document_id": document_id})
    assert response.status_code == 200, response.text
    [page] = response.json()["pages"]
    total = next(f for f in page["fields"] if f["name"] == "total")
    assert total["label"] == "合计" and total["status"] == "matched"
    # The fake model gave no merchant or date.
    assert any(i["rule"] == "required" for i in page["issues"])
    assert detail(client, document_id)["current_run"] == 1  # Trial runs are not saved.


# ---------- Export, import, delete ----------


def test_export_then_import(client, mine):
    response = client.get(f"/api/v1/templates/{mine}/export?format=json")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    data = response.json()
    assert data["format"] == "jacarandaparse-template" and data["version"] == 2

    imported = client.post("/api/v1/templates/import", json=data)
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["id"] != mine and body["version"] == 1 and body["source"] == "import"
    assert next(f for f in body["fields"] if f["name"] == "total")["label"] == "合计"

    # Starter templates can be exported too.
    starter = client.get("/api/v1/templates/cn_einvoice/export?format=json").json()
    assert starter["title"] == "中国数电发票"


def test_import_bad_file_says_why(client):
    bad = {"title": "x", "fields": [{"name": "total", "label": "总额"}], "rules": ["nope"]}
    response = client.post("/api/v1/templates/import", json=bad)
    assert response.status_code == 422
    assert "nope" in response.json()["detail"]


BLANK = {
    "source": "blank",
    "content": {"title": "空白", "fields": [{"name": "total", "label": "总额", "type": "number"}]},
}


def test_blank_template_is_created_only_with_content(client):
    """Blank template: created only when saved from the editor; not created without content."""
    assert client.post("/api/v1/templates", json={"source": "blank"}).status_code == 422
    assert [t for t in client.get("/api/v1/templates").json() if t["kind"] == "mine"] == []
    created = client.post("/api/v1/templates", json=BLANK)
    assert created.status_code == 201
    assert created.json()["title"] == "空白" and created.json()["version"] == 1


def test_delete_unused_template(client):
    created = client.post("/api/v1/templates", json=BLANK).json()
    assert client.delete(f"/api/v1/templates/{created['id']}").status_code == 204
    assert client.get(f"/api/v1/templates/{created['id']}").status_code == 404


def test_used_template_is_hidden_not_removed(client, factory, storage, receipt_page, mine):
    """Used templates can be deleted: removed from the list and upload; documents that used them
    display as usual."""
    document_id = upload(client, mine)
    process(factory, storage, document_id, receipt_page)
    assert client.delete(f"/api/v1/templates/{mine}").status_code == 204
    assert mine not in {t["id"] for t in client.get("/api/v1/templates").json()}
    assert client.get(f"/api/v1/templates/{mine}").status_code == 404
    refused = client.post(
        "/api/v1/documents", files={"file": ("r.jpg", b"y", "image/jpeg")}, data={"template": mine}
    )
    assert refused.status_code in (404, 422)
    body = detail(client, document_id)
    assert body["extractions"][0]["template_title"] == "我的收据"
    assert values(body)["total"]["label"] == "合计"
    redo = client.post(f"/api/v1/documents/{document_id}/reprocess", json={"reason": "bad_result"})
    assert redo.status_code == 422 and "已删除" in redo.json()["detail"]


def test_generic_starters_have_no_regional_rules():
    """Starter library: generic templates use only generic rules; country templates only generic
    rules and their own country's rules."""
    from jacara.plugins.validators import REGIONS

    countries = {name: load_starter(name).countries for name in STARTERS}
    assert countries["receipt_shopping"] == countries["receipt_restaurant"] == ()
    assert (
        countries["receipt_general"] == countries["intl_invoice"] == countries["hotel_folio"] == ()
    )
    assert countries["au_tax_invoice"] == ("AU",)
    assert countries["cn_einvoice"] == countries["cn_rail_ticket"] == ("CN",)
    assert countries["cn_air_itinerary"] == ("CN",)
    assert load_starter("cn_air_itinerary").domains == ("交通", "发票")
    for name in STARTERS:
        template = load_starter(name)
        assert template.foreign_rules() == [], name
        used = {REGIONS.get(r.id, "") for r in template.rules}
        assert used <= {"", *template.countries}, name
        if not template.countries:
            text = template.description + "".join(f.description for f in template.fields)
            assert "ATO" not in text and "澳元" not in text, name
    au = {r.id for r in load_starter("au_tax_invoice").rules}
    assert {"seller_abn", "au_buyer_1000", "au_tax_invoice_title"} <= au


def test_my_templates_rules_follow_fields_and_countries(client, mine):
    """Rules of my templates follow fields and countries; tags can be changed freely, and changing
    only tags creates no new version."""
    body = client.get(f"/api/v1/templates/{mine}").json()
    assert body["domains"] == ["收据"] and body["countries"] == [] and body["tags"] == []
    saved = client.put(
        f"/api/v1/templates/{mine}",
        json={
            "title": body["title"],
            "fields": body["fields"],
            "tables": body["tables"],
            "rules": ["ato_evidence"],
            "domains": ["收据", "餐饮"],
            "countries": ["au", "NZ"],
        },
    )
    assert saved.status_code == 200, saved.text
    version = saved.json()["version"]
    assert saved.json()["countries"] == ["AU", "NZ"] and saved.json()["category"] == "收据"

    labelled = client.patch(
        f"/api/v1/templates/{mine}/labels", json={"tags": ["2026-10 出差", "财务部"]}
    ).json()
    assert labelled["tags"] == ["2026-10 出差", "财务部"]
    assert labelled["countries"] == ["AU", "NZ"] and labelled["version"] == version
    bad = client.patch(f"/api/v1/templates/{mine}/labels", json={"countries": ["China"]})
    assert bad.status_code == 422 and "两位字母" in bad.json()["detail"]

    # Only name and tags changed: no new version.
    again = client.put(
        f"/api/v1/templates/{mine}",
        json={
            **{k: saved.json()[k] for k in ("fields", "tables")},
            "title": "新名字",
            "rules": ["ato_evidence"],
            "domains": ["收据"],
            "countries": ["AU"],
        },
    ).json()
    assert (
        again["version"] == version and again["title"] == "新名字" and again["countries"] == ["AU"]
    )

    # Australia removed: its rules are removed too (rules changed, so a new version).
    general = client.put(
        f"/api/v1/templates/{mine}",
        json={
            **{k: saved.json()[k] for k in ("fields", "tables")},
            "title": "新名字",
            "rules": ["ato_evidence"],
            "countries": [],
        },
    ).json()
    assert general["countries"] == [] and general["rules"] == []
