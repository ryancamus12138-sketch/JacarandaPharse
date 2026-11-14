"""Review tools: selecting from source text, adding and removing table rows, re-evaluating rules on
current values, exports following suit.

Uses receipt_page from conftest (with OCR result), total 9.00, two line items: row 1 PC 1 × 9.00
= 9.00 (correct), row 2 BAG amount 0.5 (spurious, to be deleted)."""

import dataclasses
import io
import uuid
from datetime import datetime

import pytest
from openpyxl import load_workbook

from jacara.core.db import session_scope
from jacara.core.i18n import render
from jacara.models import Extraction
from jacara.pipeline.store import save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator
from jacara.templates import load_template

DATE_LINE = 4  # "25/12/2018 8:13:39 PM" is block 5 (0-based) in receipt_page.
CHANGE_LINE = 13  # "100"
CASH_LINE = 11  # "10.00"


@pytest.fixture
def document_id(factory, receipt_page, tmp_path) -> uuid.UUID:
    fields = (
        F("merchant", "string", "BOOK TA _K (TAMAN DAYA) SDN BHD", 1),
        F("date", "date", None, None),
        F("total", "number", 9.0, 6),
        F("items.1.name", "string", "PC", 5),
        F("items.1.quantity", "number", 1.0, 5),
        F("items.1.unit_price", "number", 9.0, 5),
        F("items.1.amount", "number", 9.0, 5),
        F("items.2.name", "string", "BAG", 5),
        F("items.2.quantity", "number", None, None),
        F("items.2.unit_price", "number", None, None),
        F("items.2.amount", "number", 0.5, None),
    )
    result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
    report = DocumentReport(
        source="receipt.jpg",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator(load_template("receipt").rules).validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 11),
        ocr_page=receipt_page,
    )
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        return save_document(session, path, [report]).id


def detail(client, document_id) -> dict:
    return client.get(f"/api/v1/documents/{document_id}").json()["extractions"][0]


def by_name(client, document_id) -> dict[str, dict]:
    return {v["name"]: v for v in detail(client, document_id)["values"]}


def rows_url(client, document_id, *rest: str) -> str:
    extraction_id = detail(client, document_id)["id"]
    return "/".join([f"/api/v1/extractions/{extraction_id}/tables/items/rows", *rest])


# ---- OCR results ----


def test_page_ocr_lists_every_block(client, document_id):
    ocr = client.get(f"/api/v1/documents/{document_id}/pages/1/ocr").json()
    assert ocr["available"]
    line = ocr["lines"][DATE_LINE]
    assert line["index"] == DATE_LINE
    assert line["text"] == "25/12/2018 8:13:39 PM"
    assert line["box"] == [163, 369, 345, 390]
    assert line["row"] == 4  # Same line numbers as the model saw.


def test_old_documents_have_no_ocr(client, factory, document_id):
    with session_scope(factory) as session:
        for extraction in session.query(Extraction):
            extraction.ocr = None
    ocr = client.get(f"/api/v1/documents/{document_id}/pages/1/ocr").json()
    assert ocr == {"available": False, "lines": []}
    date = by_name(client, document_id)["date"]
    response = client.post(
        f"/api/v1/values/{date['id']}/revisions", json={"action": "edit", "source_lines": [4]}
    )
    assert response.status_code == 422
    assert "OCR" in response.json()["detail"]


# ---- Selecting from source text ----


def pick(client, value: dict, *lines: int, **extra):
    body = {"action": "edit", "source_lines": list(lines), **extra}
    return client.post(f"/api/v1/values/{value['id']}/revisions", json=body)


def test_pick_date_reads_the_text_and_moves_the_box(client, document_id):
    date = by_name(client, document_id)["date"]
    response = pick(client, date, DATE_LINE)
    assert response.status_code == 201
    picked = response.json()
    assert picked["value"] is None  # The model value is unchanged.
    assert picked["current_value"] == "2018-12-25"  # Parsed from "25/12/2018 8:13:39 PM".
    assert picked["current_evidence"]["bbox"] == [163, 369, 345, 390]
    assert picked["evidence"] is None  # The extraction-time evidence is unchanged.

    history = client.get(f"/api/v1/values/{date['id']}/revisions").json()
    [revision] = history["revisions"]
    assert revision["reason"] == "从原文选取"
    assert revision["evidence"]["row_text"] == "25/12/2018 8:13:39 PM"


def test_pick_number_and_several_blocks(client, document_id):
    values = by_name(client, document_id)
    total = pick(client, values["total"], CASH_LINE).json()
    assert total["current_value"] == "10.0"
    merchant = pick(client, values["merchant"], 1, 2).json()  # The address spans two blocks.
    assert merchant["current_value"] == "NO.55 55,57 & 59, JALAN SAGU 18, TAMAN DAYA,"


def test_pick_same_value_is_allowed_to_fix_the_box(client, document_id):
    """The value is unchanged and only the evidence changes (the model located the wrong position):
    saving still works."""
    total = by_name(client, document_id)["total"]
    response = pick(client, total, 9)  # The second "9.00", on the Total line.
    assert response.status_code == 201
    assert response.json()["current_value"] == "9.0"


def test_pick_text_without_a_number(client, document_id):
    total = by_name(client, document_id)["total"]
    response = pick(client, total, 3)  # "Date:"
    assert response.status_code == 422
    assert "没有数字" in response.json()["detail"]


def test_pick_with_a_wrong_block_number(client, document_id):
    total = by_name(client, document_id)["total"]
    assert pick(client, total, 999).status_code == 422


# ---- Adding and removing table rows ----


def test_delete_row_clears_it_and_rechecks_rules(client, document_id):
    before = detail(client, document_id)
    assert [i["rule"] for i in before["current_checks"]["issues"]] == ["items_sum"]

    response = client.post(
        rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"}
    )
    assert response.status_code == 200
    assert all(v["deleted"] for v in response.json())

    after = detail(client, document_id)
    assert after["current_checks"]["issues"] == []  # Only 9.00 remains, equal to the total.
    assert after["checks"] == before["checks"]  # Extraction-time results unchanged.
    bag = next(v for v in after["values"] if v["name"] == "items.2.amount")
    assert bag["deleted"] and bag["current_value"] == "0.5"


def test_delete_needs_a_reason_and_only_once(client, document_id):
    url = rows_url(client, document_id, "2", "delete")
    assert client.post(url, json={"reason": ""}).status_code == 422
    assert client.post(url, json={"reason": "不是商品"}).status_code == 200
    assert client.post(url, json={"reason": "再删一次"}).status_code == 422


def test_restore_row(client, document_id):
    client.post(rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"})
    response = client.post(rows_url(client, document_id, "2", "restore"), json={"reason": "删错了"})
    assert response.status_code == 200
    assert not any(v["deleted"] for v in response.json())
    assert [i["rule"] for i in detail(client, document_id)["current_checks"]["issues"]] == [
        "items_sum"
    ]


def test_deleted_cells_cannot_be_edited(client, document_id):
    client.post(rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"})
    bag = by_name(client, document_id)["items.2.name"]
    response = client.post(
        f"/api/v1/values/{bag['id']}/revisions",
        json={"action": "edit", "value": "BAG 2", "reason": "看错"},
    )
    assert response.status_code == 422
    assert "恢复" in response.json()["detail"]


def test_unknown_row(client, document_id):
    response = client.post(rows_url(client, document_id, "9", "delete"), json={"reason": "x"})
    assert response.status_code == 404


def test_add_row_after_the_first(client, document_id):
    response = client.post(rows_url(client, document_id), json={"after": 1})
    assert response.status_code == 201
    cells = response.json()
    assert [c["name"] for c in cells] == [
        "items.3.name",
        "items.3.quantity",
        "items.3.unit_price",
        "items.3.amount",
    ]  # The new number exceeds all existing ones.
    assert all(c["origin"] == "manual" and c["value"] is None for c in cells)

    names = [v["name"] for v in detail(client, document_id)["values"]]
    # Display order: between rows 1 and 2.
    assert names.index("items.1.amount") < names.index("items.3.name")
    assert names.index("items.3.amount") < names.index("items.2.name")


def test_add_row_at_the_end_and_fill_it(client, document_id):
    cells = client.post(rows_url(client, document_id), json={}).json()
    amount = next(c for c in cells if c["name"].endswith(".amount"))
    response = client.post(
        f"/api/v1/values/{amount['id']}/revisions",
        json={"action": "edit", "value": "-0.5", "reason": "漏了折扣行"},
    )
    assert response.json()["current_value"] == "-0.5"
    # 9.00 + 0.50 − 0.50 = 9.00, equal to the total.
    assert detail(client, document_id)["current_checks"]["issues"] == []
    names = [v["name"] for v in detail(client, document_id)["values"]]
    assert names[-4:] == [c["name"] for c in cells]


def test_add_row_at_the_top(client, document_id):
    cells = client.post(rows_url(client, document_id), json={"after": 0}).json()
    names = [v["name"] for v in detail(client, document_id)["values"]]
    assert names.index(cells[-1]["name"]) < names.index("items.1.name")
    # Still within the table, not before the regular fields.
    assert names.index("total") < names.index(cells[0]["name"])


def test_add_row_after_a_missing_row(client, document_id):
    assert client.post(rows_url(client, document_id), json={"after": 7}).status_code == 404


def test_rules_number_rows_as_shown(client, document_id):
    """After deleting a row, the row numbers in rule messages match the page."""
    client.post(rows_url(client, document_id, "1", "delete"), json={"reason": "重复"})
    price = by_name(client, document_id)["items.2.unit_price"]
    client.post(
        f"/api/v1/values/{price['id']}/revisions",
        json={"action": "edit", "value": "1", "reason": "补单价"},
    )
    quantity = by_name(client, document_id)["items.2.quantity"]
    client.post(
        f"/api/v1/values/{quantity['id']}/revisions",
        json={"action": "edit", "value": "2", "reason": "补数量"},
    )
    issues = detail(client, document_id)["current_checks"]["issues"]
    line = next(i for i in issues if i["rule"] == "item_line")
    assert line["message"].startswith("第 1 行：")  # Row 1 deleted; BAG is now row 1.
    assert "items.2.amount" in line["fields"]  # Field names are still the real names.


def test_audit_log_records_row_changes(client, document_id):
    client.post(rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"})
    client.post(rows_url(client, document_id), json={"after": 1})
    actions = [e["action"] for e in client.get("/api/v1/audit-logs").json()["items"]]
    assert "value:delete_row" in actions and "value:add_row" in actions


# ---- Export ----


def test_export_skips_deleted_rows_and_numbers_as_shown(client, document_id):
    client.post(rows_url(client, document_id, "1", "delete"), json={"reason": "重复"})
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    book = load_workbook(io.BytesIO(response.content))
    items = book["商品明细"]
    assert items.max_row == 2  # Header + the remaining row.
    assert [c.value for c in items[2]][:3] == ["receipt.jpg", 1, "BAG"]


def test_export_uses_the_picked_box(client, document_id):
    date = by_name(client, document_id)["date"]
    pick(client, date, DATE_LINE)
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    details = load_workbook(io.BytesIO(response.content))["明细"]
    row = next(r for r in details.iter_rows(values_only=True) if r[1] == "日期")  # Display name.
    assert row[5] == "第 4 行 (163,369)-(345,390)"


def test_report_without_ocr_still_saves(factory, report, tmp_path):
    """Reports built by the CLI and older code have no OCR result and can still be stored."""
    path = tmp_path / "r.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        document = save_document(session, path, [dataclasses.replace(report, ocr_page=None)])
        assert document.extractions[0].ocr is None


# ---- Region selection and re-recognition ----


def png(width: int = 400, height: int = 300) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def uploaded(client) -> str:
    response = client.post("/api/v1/documents", files={"file": ("r.png", png(), "image/png")})
    return response.json()["id"]


def test_region_is_cropped_enlarged_and_read(client, uploaded):
    from .conftest import region_ocr

    region_ocr.lines = ["SUMMERINA 1.4KG", "9.99"]
    region_ocr.seen.clear()
    response = client.post(
        f"/api/v1/documents/{uploaded}/pages/1/ocr-region", json={"box": [100, 100, 200, 120]}
    )
    assert response.status_code == 200
    assert response.json() == {"text": "SUMMERINA 1.4KG 9.99", "confidence": 0.9}
    # 8 pixels of padding on each side: crop (92, 92)-(208, 128) = 116 × 36, too short, upscaled to
    # 96 tall.
    assert region_ocr.seen == [(309, 96)]


def test_region_falls_back_to_reading_it_as_one_line(client, uploaded):
    """Detection finds no text (too small, touching the edge): the whole region is recognized as one
    line."""
    from .conftest import region_ocr

    region_ocr.lines, region_ocr.line = [], ("84998650", 0.99)
    try:
        response = client.post(
            f"/api/v1/documents/{uploaded}/pages/1/ocr-region", json={"box": [10, 10, 90, 30]}
        )
    finally:
        region_ocr.line = None
    assert response.json() == {"text": "84998650", "confidence": 0.99}


def test_region_outside_the_page_is_rejected(client, uploaded):
    response = client.post(
        f"/api/v1/documents/{uploaded}/pages/1/ocr-region", json={"box": [900, 900, 950, 950]}
    )
    assert response.status_code == 422
    assert "过小" in response.json()["detail"]


def test_region_needs_four_numbers(client, uploaded):
    response = client.post(
        f"/api/v1/documents/{uploaded}/pages/1/ocr-region", json={"box": [1, 2, 3]}
    )
    assert response.status_code == 422


def test_save_value_from_a_drawn_box(client, document_id):
    total = by_name(client, document_id)["total"]
    response = client.post(
        f"/api/v1/values/{total['id']}/revisions",
        json={"action": "edit", "value": "9.99", "source_box": [410, 640, 380, 660]},
    )
    assert response.status_code == 201
    saved = response.json()
    assert saved["current_value"] == "9.99"
    assert saved["current_evidence"]["bbox"] == [380, 640, 410, 660]  # Reverse dragging works too.
    assert saved["current_evidence"]["drawn"] is True
    history = client.get(f"/api/v1/values/{total['id']}/revisions").json()
    assert history["revisions"][0]["reason"] == "框选重新识别"


def test_drawn_box_needs_a_value(client, document_id):
    total = by_name(client, document_id)["total"]
    response = client.post(
        f"/api/v1/values/{total['id']}/revisions",
        json={"action": "edit", "value": "", "source_box": [1, 2, 3, 4]},
    )
    assert response.status_code == 422


def test_pick_and_box_together_is_an_error(client, document_id):
    total = by_name(client, document_id)["total"]
    response = client.post(
        f"/api/v1/values/{total['id']}/revisions",
        json={"action": "edit", "value": "1", "source_box": [1, 2, 3, 4], "source_lines": [1]},
    )
    assert response.status_code == 422


# ---- Bulk confirmation ----


def test_confirm_many_clears_the_yellow(client, document_id):
    values = by_name(client, document_id)
    pending = [v["id"] for v in values.values() if v["pending_review"]]
    assert pending
    response = client.post("/api/v1/values/confirm", json={"ids": pending})
    assert response.json() == {"confirmed": len(pending), "skipped": 0}
    after = by_name(client, document_id)
    assert not any(v["pending_review"] for v in after.values())
    history = client.get(f"/api/v1/values/{pending[0]}/revisions").json()
    assert history["revisions"][0]["action"] == "confirm"
    assert history["revisions"][0]["reason"] == "批量确认"
    actions = [e["action"] for e in client.get("/api/v1/audit-logs").json()["items"]]
    assert actions.count("value:confirm_many") == 1  # One audit log entry only.


def test_confirm_many_skips_deleted_rows(client, document_id):
    client.post(rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"})
    values = by_name(client, document_id)
    ids = [values["items.1.name"]["id"], values["items.2.name"]["id"]]
    response = client.post("/api/v1/values/confirm", json={"ids": ids, "reason": "核对过"})
    assert response.json() == {"confirmed": 1, "skipped": 1}


def test_confirm_many_unknown_id(client, document_id):
    response = client.post("/api/v1/values/confirm", json={"ids": [str(uuid.uuid4())]})
    assert response.status_code == 404


# ---- Reprocessing several documents ----


def test_reprocess_many_with_default_settings(
    client, factory, queue, storage, receipt_page, tmp_path
):
    from jacara.models import Document

    ids = []
    for n in range(3):
        fields = (F("total", "number", 9.0, 6),)
        result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
        report = DocumentReport(
            source="r.jpg",
            extraction=result,
            aligned=TextMatchAligner().align(receipt_page, result),
            checks=RuleValidator().validate(fields),
            ocr_engine="rapidocr test",
            processed_at=datetime(2026, 11, 13),
            ocr_page=receipt_page,
        )
        path = tmp_path / f"r{n}.jpg"
        path.write_bytes(f"{n}".encode())
        with session_scope(factory) as session:
            document = save_document(session, path, [report])
            document.storage_key = f"documents/r{n}"
            document.vision = "always"  # Last time "always show the image" was selected manually.
            ids.append(document.id)
    with session_scope(factory) as session:
        session.get_one(Document, ids[2]).status = "processing"
    response = client.post("/api/v1/documents/reprocess", json={"ids": [str(i) for i in ids]})
    assert response.status_code == 202
    body = response.json()
    assert body["queued"] == 2
    assert body["skipped"] == ["r2.jpg：正在处理"]
    assert queue.sent == ids[:2]
    with session_scope(factory) as session:
        for document_id in ids[:2]:
            document = session.get_one(Document, document_id)
            assert document.status == "uploaded"
            assert document.vision is None  # Back to the system setting.
            assert render(document.run_reason) == "批量重新处理"


def test_reprocess_many_with_chosen_settings(client, factory, queue, document_id):
    from jacara.models import Document

    with session_scope(factory) as session:
        session.get_one(Document, document_id).storage_key = "documents/x"
    body = {
        "ids": [str(document_id)],
        "template": "discover",
        "split": "pages",
        "vision": "off",
        "pdf_read": None,  # Same as last time.
        "reason": "wrong_template",
    }
    assert client.post("/api/v1/documents/reprocess", json=body).status_code == 202
    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        assert (document.discover, document.split, document.vision) == (True, "pages", "off")
        assert render(document.run_reason) == "模板选择错误"
    other = {"ids": [str(document_id)], "reason": "other"}
    assert client.post("/api/v1/documents/reprocess", json=other).status_code == 422
    ranges = {"ids": [str(document_id)], "split": "1-2"}
    assert client.post("/api/v1/documents/reprocess", json=ranges).status_code == 422


def test_reprocess_many_needs_the_queue(client, factory, queue, document_id):
    from jacara.models import Document

    with session_scope(factory) as session:
        session.get_one(Document, document_id).storage_key = "documents/x"
    queue.down = True
    response = client.post("/api/v1/documents/reprocess", json={"ids": [str(document_id)]})
    assert response.status_code == 503
    with session_scope(factory) as session:
        # Same as an upload whose queue was unreachable: queued and retryable from the list.
        assert session.get_one(Document, document_id).status == "uploaded"


# ---- JSON export ----


def test_export_json(client, document_id):
    import json

    total = by_name(client, document_id)["total"]
    client.post(
        f"/api/v1/values/{total['id']}/revisions",
        json={"action": "edit", "value": "9.50", "reason": "测试"},
    )
    response = client.get("/api/v1/documents/export.json", params={"ids": [str(document_id)]})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    content = json.loads(response.content.decode("utf-8"))
    assert content["format"] == "jacarandaparse.export.v1"
    [document] = content["documents"]
    assert document["filename"] == "receipt.jpg"
    [unit] = document["units"]
    assert unit["pages"] == [1, 1]
    assert unit["fields"]["total"] == 9.5  # Post-review values; numbers are numbers.
    assert unit["fields"]["date"] is None
    assert [row["name"] for row in unit["tables"]["items"]] == ["PC", "BAG"]
    assert unit["tables"]["items"][0]["amount"] == 9.0
    assert unit["labels"]["total"]  # Field names.
    # Edited by a person, so no longer awaiting confirmation.
    assert "total" not in unit["needs_review"]
    assert isinstance(unit["issues"], list)
    exports = client.get("/api/v1/exports").json()["items"]
    assert exports[0]["format"] == "json"  # Also in the export history.


def test_sent_text_and_summary(client, factory, storage, receipt_page):
    """The review page shows which text blocks were sent to the model in this run."""
    from .test_template_library import process, upload

    document_id = upload(client)
    process(factory, storage, document_id, receipt_page)
    [extraction] = client.get(f"/api/v1/documents/{document_id}").json()["extractions"]
    sent = extraction["sent"]
    assert sent["blocks"] == sent["total_blocks"] == len(receipt_page.lines)
    assert sent["has_snapshot"] is True
    text = client.get(f"/api/v1/documents/{document_id}/extractions/{extraction['id']}/sent").json()
    assert text["blocks"][0] == 1 and receipt_page.lines[0].text in text["text"]
    missing = client.get(f"/api/v1/documents/{document_id}/extractions/{uuid.uuid4()}/sent")
    assert missing.status_code == 404
