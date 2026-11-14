"""Interface language of API responses: errors, processing results and files follow
Accept-Language; stored messages are rendered for each reader."""

from __future__ import annotations

import io
import json
import uuid

from openpyxl import load_workbook

from .test_template_library import process, upload
from .test_workbook import XLSX, build, pharma, upload_excel

ENGLISH = {"Accept-Language": "en-US,en;q=0.9"}
STORED_MARKERS = ('{"msg":', '{"join":')


def test_errors_follow_accept_language(client):
    missing = f"/api/v1/documents/{uuid.uuid4()}"
    assert client.get(missing).json()["detail"] == "文档不存在"
    english = client.get(missing, headers=ENGLISH)
    assert english.status_code == 404
    assert english.json()["detail"] == "Document not found"


def test_processing_results_are_rendered_for_each_reader(client, factory, storage, receipt_page):
    document_id = upload(client)
    assert process(factory, storage, document_id, receipt_page) == "done"
    chinese = client.get(f"/api/v1/documents/{document_id}").json()
    english = client.get(f"/api/v1/documents/{document_id}", headers=ENGLISH).json()
    # Stored messages never reach the frontend as JSON.
    for body in (chinese, english):
        text = json.dumps(body, ensure_ascii=False)
        assert not any(marker in text for marker in STORED_MARKERS)
    notes = {v["name"]: v["note"] for e in english["extractions"] for v in e["values"] if v["note"]}
    original = {
        v["name"]: v["note"] for e in chinese["extractions"] for v in e["values"] if v["note"]
    }
    assert notes["store_name"] == "The model says this field is absent from the source text"
    assert original["store_name"] == "模型判断原文中没有此字段"


def test_standard_workbook_is_written_in_the_reader_language_and_read_in_any(client):
    template = build(client, upload_excel(client, pharma()), "实验数据")
    exported = client.get(f"/api/v1/templates/{template['id']}/export", headers=ENGLISH)
    assert exported.headers["content-type"] == XLSX
    book = load_workbook(io.BytesIO(exported.content))
    assert {"Template", "Fields", "Tables", "Rules", "Entities", "Reference tables"} <= set(
        book.sheetnames
    )
    assert [c.value for c in book["Fields"][1]][:3] == ["Name", "Field ID", "Type"]

    # A workbook exported from the English interface is imported from the Chinese one.
    analysis = upload_excel(client, exported.content, "lab.xlsx")
    assert analysis["kind"] == "standard"
    imported = client.post("/api/v1/templates/import", json=analysis["template"]).json()
    assert [f["name"] for f in imported["fields"]] == [f["name"] for f in template["fields"]]
    assert [f["type"] for f in imported["fields"]] == [f["type"] for f in template["fields"]]
    assert imported["references"][0]["row_count"] == 2


def test_excel_export_uses_the_reader_language(client, factory, storage, receipt_page):
    document_id = upload(client)
    assert process(factory, storage, document_id, receipt_page) == "done"
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx", headers=ENGLISH)
    book = load_workbook(io.BytesIO(response.content))
    assert {"Summary", "Line items", "Source"} <= set(book.sheetnames)
    chinese = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    assert {"汇总", "明细", "来源"} <= set(load_workbook(io.BytesIO(chinese.content)).sheetnames)


# ---------- Templates in several languages ----------


def test_starters_switch_their_primary_language_and_back():
    from jacara.templates import (
        STARTERS,
        load_starter,
        load_starter_in,
        parse_template,
        with_language,
    )

    for name in STARTERS:
        chinese, english = load_starter(name), load_starter_in(name, "en")
        assert english.language == "en" and dict(english.titles)["zh"] == chinese.title
        assert english.title == dict(chinese.titles)["en"]
        assert len(english.basis) == len(chinese.basis)
        assert all(d.isascii() for d in english.domains)
        pairs = [(chinese.fields, english.fields)]
        pairs += [
            (a.columns, b.columns) for a, b in zip(chinese.tables, english.tables, strict=True)
        ]
        for before, after in pairs:
            for a, b in zip(before, after, strict=True):
                assert b.label == a.label_in("en") and b.label_in("zh") == a.label
                # Every description of the starter library has an English version.
                assert bool(a.description) == bool(b.description)
                assert not a.description or b.description != a.description
        # Switching back restores the original definition.
        back = parse_template(with_language(english.definition() | _head(english), "zh"), name)
        assert back.definition() == chinese.definition()
        assert (back.title, back.description, back.domains) == (
            chinese.title,
            chinese.description,
            chinese.domains,
        )


def _head(t):
    return {
        "name": t.name,
        "version": t.version,
        "title": t.title,
        "description": t.description,
        "category": t.category,
        "domains": list(t.domains),
        "countries": list(t.countries),
    }


def test_starter_library_follows_the_interface_language(client):
    chinese = {t["name"]: t for t in client.get("/api/v1/templates/starters").json()}
    english = {
        t["name"]: t for t in client.get("/api/v1/templates/starters", headers=ENGLISH).json()
    }
    au_zh, au = chinese["au_tax_invoice"], english["au_tax_invoice"]
    assert au_zh["language"] == "zh" and au_zh["title"] == "澳洲税务发票"
    assert au["language"] == "en" and au["languages"][0] == "en"
    assert au["title"] == "Australian Tax Invoice" and au["titles"] == {"zh": "澳洲税务发票"}
    assert au["domains"] == ["Invoice"] and au["basis"][0] == "For: Australian tax invoices."
    seller = next(f for f in au["fields"] if f["name"] == "seller_name")
    assert seller["label"] == "Supplier Name" and seller["names"] == {"zh": "卖方名称"}
    assert seller["description"].startswith("The name") and seller["descriptions"]["zh"]


def test_template_keeps_its_own_primary_language(client):
    created = client.post(
        "/api/v1/templates",
        json={"source": "starter", "starter": "au_tax_invoice"},
        headers=ENGLISH,
    ).json()
    assert created["language"] == "en" and created["title"] == "Australian Tax Invoice"
    assert created["domains"] == ["Invoice"]
    # Read from the Chinese interface: still an English template, Chinese one switch away.
    shown = client.get(f"/api/v1/templates/{created['id']}").json()
    assert shown["language"] == "en" and shown["titles"]["zh"] == "澳洲税务发票"

    body = {
        "title": shown["title"],
        "titles": shown["titles"],
        "language": "en",
        "description": shown["description"],
        "descriptions": shown["descriptions"],
        "fields": shown["fields"],
        "tables": shown["tables"],
        "rules": [r["id"] for r in shown["rules"]],
        "domains": shown["domains"],
        "countries": shown["countries"],
        "note": "same",
    }
    saved = client.put(f"/api/v1/templates/{created['id']}", json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["language"] == "en"
    assert saved.json()["descriptions"]["zh"].startswith("澳洲的 Tax invoice")

    bad = client.put(f"/api/v1/templates/{created['id']}", json={**body, "language": "english"})
    assert bad.status_code == 422 and "english" in bad.json()["detail"]

    exported = client.get(f"/api/v1/templates/{created['id']}/export?format=json").json()
    assert exported["language"] == "en" and exported["titles"] == {"zh": "澳洲税务发票"}
    imported = client.post("/api/v1/templates/import", json=exported).json()
    assert imported["language"] == "en" and imported["descriptions"] == shown["descriptions"]
    item = next(f for f in imported["fields"] if f["name"] == "seller_name")
    assert item["names"] == {"zh": "卖方名称"} and item["descriptions"]["zh"]


def test_workbook_headers_match_starter_fields_in_any_language():
    from jacara.templates import load_starter
    from jacara.workbook.analyze import _match  # pyright: ignore[reportPrivateUsage]

    seller = next(f for f in load_starter("au_tax_invoice").fields if f.name == "seller_name")
    assert _match(seller, "卖方名称") and _match(seller, "Supplier Name")
