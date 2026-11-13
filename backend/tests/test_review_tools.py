"""审核工具（ADR 0027）：从原文选取、表格增删行、规则按当前值重新检查、导出跟着变。

文档用 conftest 的 receipt_page（带 OCR 结果），总额 9.00，商品明细两行：
第 1 行 PC 1 × 9.00 = 9.00（对的），第 2 行 BAG 金额 0.5（多出来的、要删掉的）。
"""

import dataclasses
import io
import uuid
from datetime import datetime

import pytest
from openpyxl import load_workbook

from jacara.core.db import session_scope
from jacara.models import Extraction
from jacara.pipeline.store import save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator
from jacara.templates import load_template

DATE_LINE = 4  # receipt_page 里 "25/12/2018 8:13:39 PM" 是第 5 块（从 0 数）
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


# ---- OCR 结果 ----


def test_page_ocr_lists_every_block(client, document_id):
    ocr = client.get(f"/api/v1/documents/{document_id}/pages/1/ocr").json()
    assert ocr["available"]
    line = ocr["lines"][DATE_LINE]
    assert line["index"] == DATE_LINE
    assert line["text"] == "25/12/2018 8:13:39 PM"
    assert line["box"] == [163, 369, 345, 390]
    assert line["row"] == 4  # 和模型看到的行号一样


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


# ---- 从原文选取 ----


def pick(client, value: dict, *lines: int, **extra):
    body = {"action": "edit", "source_lines": list(lines), **extra}
    return client.post(f"/api/v1/values/{value['id']}/revisions", json=body)


def test_pick_date_reads_the_text_and_moves_the_box(client, document_id):
    date = by_name(client, document_id)["date"]
    response = pick(client, date, DATE_LINE)
    assert response.status_code == 201
    picked = response.json()
    assert picked["value"] is None  # 模型原值不变
    assert picked["current_value"] == "2018-12-25"  # 从 "25/12/2018 8:13:39 PM" 读出
    assert picked["current_evidence"]["bbox"] == [163, 369, 345, 390]
    assert picked["evidence"] is None  # 抽取时的出处不变

    history = client.get(f"/api/v1/values/{date['id']}/revisions").json()
    [revision] = history["revisions"]
    assert revision["reason"] == "从原文选取"
    assert revision["evidence"]["row_text"] == "25/12/2018 8:13:39 PM"


def test_pick_number_and_several_blocks(client, document_id):
    values = by_name(client, document_id)
    total = pick(client, values["total"], CASH_LINE).json()
    assert total["current_value"] == "10.0"
    merchant = pick(client, values["merchant"], 1, 2).json()  # 地址跨两块
    assert merchant["current_value"] == "NO.55 55,57 & 59, JALAN SAGU 18, TAMAN DAYA,"


def test_pick_same_value_is_allowed_to_fix_the_box(client, document_id):
    """值没变，只是出处换了（模型找错了位置）：也能保存。"""
    total = by_name(client, document_id)["total"]
    response = pick(client, total, 9)  # 第二个 "9.00"，在 Total 那一行
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


# ---- 表格增删行 ----


def test_delete_row_clears_it_and_rechecks_rules(client, document_id):
    before = detail(client, document_id)
    assert [i["rule"] for i in before["current_checks"]["issues"]] == ["items_sum"]

    response = client.post(
        rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"}
    )
    assert response.status_code == 200
    assert all(v["deleted"] for v in response.json())

    after = detail(client, document_id)
    assert after["current_checks"]["issues"] == []  # 只剩 9.00，等于总额
    assert after["checks"] == before["checks"]  # 抽取时的结果不变
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
    ]  # 新编号比现有的都大
    assert all(c["origin"] == "manual" and c["value"] is None for c in cells)

    names = [v["name"] for v in detail(client, document_id)["values"]]
    # 显示顺序：插在第 1 行和第 2 行中间
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
    # 9.00 + 0.50 − 0.50 = 9.00，等于总额
    assert detail(client, document_id)["current_checks"]["issues"] == []
    names = [v["name"] for v in detail(client, document_id)["values"]]
    assert names[-4:] == [c["name"] for c in cells]


def test_add_row_at_the_top(client, document_id):
    cells = client.post(rows_url(client, document_id), json={"after": 0}).json()
    names = [v["name"] for v in detail(client, document_id)["values"]]
    assert names.index(cells[-1]["name"]) < names.index("items.1.name")
    assert names.index("total") < names.index(cells[0]["name"])  # 还在表格里，不跑到普通字段前面


def test_add_row_after_a_missing_row(client, document_id):
    assert client.post(rows_url(client, document_id), json={"after": 7}).status_code == 404


def test_rules_number_rows_as_shown(client, document_id):
    """删掉一行之后，规则说的"第几行"和页面上的一致。"""
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
    assert line["message"].startswith("第 1 行：")  # 第 1 行删了，BAG 现在是第 1 行
    assert "items.2.amount" in line["fields"]  # 字段名还是真实的名字


def test_audit_log_records_row_changes(client, document_id):
    client.post(rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"})
    client.post(rows_url(client, document_id), json={"after": 1})
    actions = [e["action"] for e in client.get("/api/v1/audit-logs").json()["items"]]
    assert "value:delete_row" in actions and "value:add_row" in actions


# ---- 导出 ----


def test_export_skips_deleted_rows_and_numbers_as_shown(client, document_id):
    client.post(rows_url(client, document_id, "1", "delete"), json={"reason": "重复"})
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    book = load_workbook(io.BytesIO(response.content))
    items = book["商品明细"]
    assert items.max_row == 2  # 表头 + 剩下的一行
    assert [c.value for c in items[2]][:3] == ["receipt.jpg", 1, "BAG"]


def test_export_uses_the_picked_box(client, document_id):
    date = by_name(client, document_id)["date"]
    pick(client, date, DATE_LINE)
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    details = load_workbook(io.BytesIO(response.content))["明细"]
    row = next(r for r in details.iter_rows(values_only=True) if r[1] == "日期")  # 中文名
    assert row[5] == "第 4 行 (163,369)-(345,390)"


def test_report_without_ocr_still_saves(factory, report, tmp_path):
    """命令行和旧代码造的报告没有 OCR 结果：照样能存。"""
    path = tmp_path / "r.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        document = save_document(session, path, [dataclasses.replace(report, ocr_page=None)])
        assert document.extractions[0].ocr is None


# ---- 框选、重新识别（5b） ----


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
    # 四周各多留 8 像素：裁下 (92, 92)-(208, 128) = 116 × 36，太矮，放大到 96 高
    assert region_ocr.seen == [(309, 96)]


def test_region_falls_back_to_reading_it_as_one_line(client, uploaded):
    """检测一个字都没找到（字太小、贴着边）：整块当成一行直接识别。"""
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
    assert "框太小" in response.json()["detail"]


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
    assert saved["current_evidence"]["bbox"] == [380, 640, 410, 660]  # 反着拖也行
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


# ---- 批量确认 ----


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
    assert actions.count("value:confirm_many") == 1  # 审计日志只记一条


def test_confirm_many_skips_deleted_rows(client, document_id):
    client.post(rows_url(client, document_id, "2", "delete"), json={"reason": "不是商品"})
    values = by_name(client, document_id)
    ids = [values["items.1.name"]["id"], values["items.2.name"]["id"]]
    response = client.post("/api/v1/values/confirm", json={"ids": ids, "reason": "核对过"})
    assert response.json() == {"confirmed": 1, "skipped": 1}


def test_confirm_many_unknown_id(client, document_id):
    response = client.post("/api/v1/values/confirm", json={"ids": [str(uuid.uuid4())]})
    assert response.status_code == 404


# ---- 一次重新处理几份 ----


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
            document.vision = "always"  # 上次手动选了"每张都看原图"
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
            assert document.vision is None  # 回到系统设置
            assert document.run_reason == "批量重新处理"


def test_reprocess_many_with_chosen_settings(client, factory, queue, document_id):
    from jacara.models import Document

    with session_scope(factory) as session:
        session.get_one(Document, document_id).storage_key = "documents/x"
    body = {
        "ids": [str(document_id)],
        "template": "discover",
        "split": "pages",
        "vision": "off",
        "pdf_read": None,  # 和上次一样
        "reason": "wrong_template",
    }
    assert client.post("/api/v1/documents/reprocess", json=body).status_code == 202
    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        assert (document.discover, document.split, document.vision) == (True, "pages", "off")
        assert document.run_reason == "选错了模板"
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
        # 和上传时队列没连上一样：排队中，列表上可以"重试"
        assert session.get_one(Document, document_id).status == "uploaded"


# ---- 导出 JSON ----


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
    assert unit["fields"]["total"] == 9.5  # 审核之后的值，数字是数字
    assert unit["fields"]["date"] is None
    assert [row["name"] for row in unit["tables"]["items"]] == ["PC", "BAG"]
    assert unit["tables"]["items"][0]["amount"] == 9.0
    assert unit["labels"]["total"]  # 字段名
    assert "total" not in unit["needs_review"]  # 人改过了，不算等人确认
    assert isinstance(unit["issues"], list)
    exports = client.get("/api/v1/exports").json()["items"]
    assert exports[0]["format"] == "json"  # 导出记录里也有


def test_sent_text_and_summary(client, factory, storage, receipt_page):
    """审核页能看到这一轮发给模型的是哪些文字块（ADR 0053）。"""
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
