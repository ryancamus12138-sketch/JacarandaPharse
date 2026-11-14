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
    assert client.get(missing).json()["detail"] == "找不到这个文档"
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
    assert original["store_name"] == "模型说原文里没有这个字段"


def test_standard_workbook_is_written_in_the_reader_language_and_read_in_any(client):
    template = build(client, upload_excel(client, pharma()), "实验数据")
    exported = client.get(f"/api/v1/templates/{template['id']}/export", headers=ENGLISH)
    assert exported.headers["content-type"] == XLSX
    book = load_workbook(io.BytesIO(exported.content))
    assert {"Template", "Fields", "Tables", "Rules", "Entities", "Reference tables"} <= set(
        book.sheetnames
    )
    assert [c.value for c in book["Fields"][1]][:3] == ["Display name", "Field ID", "Type"]

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
