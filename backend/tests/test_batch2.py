"""Garbled text layers fall back to OCR; duplicate checks; splitting a file into documents; batch
import."""

from __future__ import annotations

from jacara.plugins.ocr.loader import MAX_GARBLED, garbled_ratio


def test_garbled_text_layer_is_detected():
    assert garbled_ratio("Invoice No. 12345 Total 9.00") == 0
    assert garbled_ratio("发票号码 12345 价税合计 ¥9.00") == 0
    assert garbled_ratio("(cid:12)(cid:7)(cid:44) (cid:3)(cid:9) 12") > MAX_GARBLED
    assert garbled_ratio("CafÃ© â€œTotalâ€\u009d") > MAX_GARBLED
    assert garbled_ratio(" abc") > MAX_GARBLED
    assert garbled_ratio("") == 0


# ---------- Splitting a file into documents ----------

import uuid  # noqa: E402

from jacara.pipeline.process import process_document  # noqa: E402
from jacara.pipeline.run import Pipeline  # noqa: E402
from jacara.pipeline.segment import SplitError, normalize_split, page_marker, segments  # noqa: E402
from jacara.plugins.extractors import LlmExtractor  # noqa: E402

from .conftest import make_line, make_page, raises  # noqa: E402
from .test_template_library import SchemaBackend, detail, upload  # noqa: E402


def hotel_pages():
    first = make_page(
        make_line("OYO Hotel Nanganallur", 10, 10, 300, 30),
        make_line("Folio No. F-100", 10, 40, 300, 60),
        make_line("Page 1 of 2", 10, 900, 200, 920),
    )
    second = make_page(
        make_line("Room charges 31/12", 10, 10, 300, 30),
        make_line("Grand Total 1939", 10, 40, 300, 60),
        make_line("Page 2 of 2", 10, 900, 200, 920),
    )
    receipt = make_page(
        make_line("CAFE ROMA", 10, 10, 300, 30),
        make_line("TOTAL 12.50", 10, 40, 300, 60),
    )
    return [first, second, receipt]


def test_page_markers_and_split_modes():
    first, second, receipt = hotel_pages()
    assert page_marker(first) == "starts" and page_marker(second) == "continues"
    assert page_marker(receipt) is None
    assert page_marker(make_page(make_line("第 2 页 共 3 页", 0, 0, 10, 10))) == "continues"
    assert page_marker(make_page(make_line("发票（续）", 0, 0, 10, 10))) == "continues"
    pages = hotel_pages()
    assert segments(pages, "auto") == [(1, 2), (3, 3)]
    assert segments(pages, "auto", judge=lambda a, b: True) == [(1, 3)]
    assert segments(pages, "one") == [(1, 3)]
    assert segments(pages, "pages") == [(1, 1), (2, 2), (3, 3)]
    # Pages not covered are detected automatically, starting after the last covered page.
    assert segments(pages, "1") == [(1, 1), (2, 2), (3, 3)]
    assert segments(pages, "1-2, 3") == [(1, 2), (3, 3)]
    assert normalize_split(" 1 - 2，3 ") == "1-2, 3"
    with raises(SplitError, "应该从第 3 页开始"):
        normalize_split("1-2, 4")


HOTEL = {
    "hotel": ("OYO Hotel Nanganallur", 1),
    "folio_no": ("F-100", 2),
    "total": (1939, 5),
}


def test_two_page_folio_is_one_document(client, factory, storage, monkeypatch):
    created = client.post("/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"})
    template_id = created.json()["id"]
    document_id = upload(client, template_id)
    pipeline = Pipeline(ocr=None, extractor=LlmExtractor(SchemaBackend(HOTEL)))  # type: ignore[arg-type]
    monkeypatch.setattr(pipeline, "read", lambda _path, _how=None: hotel_pages())
    outcome = process_document(
        uuid.UUID(document_id), storage=storage, pipeline=pipeline, factory=factory
    )
    assert outcome == "done"

    body = detail(client, document_id)
    assert body["page_count"] == 3
    first, second = body["extractions"]
    assert (first["page"], first["last_page"]) == (1, 2)
    assert (second["page"], second["last_page"]) == (3, None)
    values = {v["name"]: v for v in first["values"]}
    # The total is on page 2: the evidence records page 2 in page 2's own coordinates.
    total = values["total"]
    assert total["current_value"] == "1939.0"
    assert total["evidence"]["page"] == 2
    assert total["evidence"]["bbox"][1] == 40
    assert values["hotel"]["evidence"]["page"] == 1

    ocr = client.get(f"/api/v1/documents/{document_id}/pages/2/ocr").json()
    assert [line["text"] for line in ocr["lines"]][:2] == ["Room charges 31/12", "Grand Total 1939"]
    structure = client.get(f"/api/v1/documents/{document_id}/pages/2/structure").json()
    assert structure["available"]

    bad = client.post(
        f"/api/v1/documents/{document_id}/reprocess",
        json={"reason": "bad_result", "split": "2-3"},
    )
    assert bad.status_code == 422 and "从第 1 页开始" in bad.json()["detail"]
    # Switch to one document per page: 3 documents after reprocessing.
    redo = client.post(
        f"/api/v1/documents/{document_id}/reprocess",
        json={"reason": "bad_result", "split": "pages"},
    )
    assert redo.status_code == 202, redo.text
    assert redo.json()["split"] == "pages"


# ---------- Duplicate checks, batch import ----------


def upload_file(client, name="r.jpg", data=b"x", **form):
    response = client.post(
        "/api/v1/documents", files={"file": (name, data, "image/jpeg")}, data=form
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_same_file_twice_and_folders(client):
    first = upload_file(client, folder="2026-10 出差/酒店")
    again = upload_file(client, name="copy.jpg", folder="./2026-10 出差\\餐饮/")
    assert first["same_file"] == []
    assert again["same_file"][0]["id"] == first["id"]
    assert again["folder"] == "2026-10 出差/餐饮"
    skipped = upload_file(client, name="third.jpg", skip_duplicate="true")
    assert skipped["duplicate_skipped"] and skipped["id"] == first["id"]
    other = upload_file(client, name="other.jpg", data=b"y", folder="其他")

    folders = client.get("/api/v1/documents/folders").json()
    assert folders == [
        {"folder": "2026-10 出差/酒店", "count": 1},
        {"folder": "2026-10 出差/餐饮", "count": 1},
        {"folder": "其他", "count": 1},
    ]
    inside = client.get("/api/v1/documents", params={"folder": "2026-10 出差"}).json()
    assert {d["id"] for d in inside["items"]} == {first["id"], again["id"]}
    assert other["id"] not in {d["id"] for d in inside["items"]}
    bad = client.post(
        "/api/v1/documents",
        files={"file": ("r.jpg", b"z", "image/jpeg")},
        data={"split": "3-4"},
    )
    assert bad.status_code == 422


def test_same_invoice_number_is_flagged(client, factory, storage, monkeypatch):
    template_id = client.post(
        "/api/v1/templates", json={"source": "starter", "starter": "hotel_folio"}
    ).json()["id"]
    ids = []
    for data in (b"a", b"b"):
        document_id = upload_file(client, data=data, template=template_id, split="one")["id"]
        pipeline = Pipeline(ocr=None, extractor=LlmExtractor(SchemaBackend(HOTEL)))  # type: ignore[arg-type]
        monkeypatch.setattr(pipeline, "read", lambda _path, _how=None: hotel_pages()[:2])
        process_document(
            uuid.UUID(document_id), storage=storage, pipeline=pipeline, factory=factory
        )
        ids.append(document_id)

    [extraction] = detail(client, ids[1])["extractions"]
    issues = [i for i in extraction["current_checks"]["issues"] if i["rule"] == "duplicate"]
    assert len(issues) == 1 and issues[0]["severity"] == "warning"
    assert "账单号「F-100」和《r.jpg》第 1–2 页" in issues[0]["message"]
    folio = next(v for v in extraction["values"] if v["name"] == "folio_no")
    assert folio["check_state"] == "warn"

    # Change the bill number of one document: no more warning.
    revise = client.post(
        f"/api/v1/values/{folio['id']}/revisions",
        json={"action": "edit", "value": "F-101", "reason": "看错了"},
    )
    assert revise.status_code == 201
    [extraction] = detail(client, ids[1])["extractions"]
    assert "duplicate" in extraction["current_checks"]["passed"]


def test_watch_folder_imports_new_files_once(tmp_path, factory, storage, queue):
    from jacara.core.db import session_scope
    from jacara.core.owner import PERSONAL_OWNER
    from jacara.models import Document
    from jacara.pipeline.watch import Watcher

    root = tmp_path / "收件箱"
    (root / "酒店").mkdir(parents=True)
    (root / "酒店" / "a.jpg").write_bytes(b"one")
    (root / "b.pdf").write_bytes(b"%PDF-1.4 not really")
    (root / ".DS_Store").write_bytes(b"x")
    (root / "notes.txt").write_text("x")
    clock = [10_000_000_000.0]
    watcher = Watcher(root, factory, storage, queue, PERSONAL_OWNER, now=lambda: clock[0])
    done = {p.name: r for p, r in watcher.scan()}
    assert done["a.jpg"] == "导入"
    assert "b.pdf" in done and ".DS_Store" not in done and "notes.txt" not in done
    assert watcher.scan() == []  # Unchanged files are not examined again.

    (root / "copy.jpg").write_bytes(b"one")  # Same content: previously imported.
    assert {p.name: r for p, r in watcher.scan()} == {"copy.jpg": "以前导入过"}
    with session_scope(factory) as session:
        folders = sorted(d.folder or "" for d in session.query(Document).all())
    assert "收件箱/酒店" in folders


# ---------- Deleting documents ----------


def test_delete_documents_one_and_many(client, factory, storage, receipt_page):
    from jacara.core.db import session_scope
    from jacara.models import Comment, Document, ExtractedValue, Extraction

    from .test_template_library import process

    ids = [upload_file(client, data=d)["id"] for d in (b"1", b"2", b"3")]
    process(factory, storage, ids[0], receipt_page)
    client.post(f"/api/v1/documents/{ids[0]}/comments", json={"body": "看一下"})
    # Three originals + the OCR snapshot of the processed document.
    assert len(storage.files) == 4
    assert any(k.startswith(f"ocr-snapshots/{ids[0]}/") for k in storage.files)

    one = client.delete(f"/api/v1/documents/{ids[0]}")
    assert one.status_code == 200 and one.json() == {"deleted": 1, "skipped": []}
    assert client.get(f"/api/v1/documents/{ids[0]}").status_code == 404
    with session_scope(factory) as session:
        assert session.query(Extraction).count() == 0
        assert session.query(ExtractedValue).count() == 0
        assert session.query(Comment).count() == 0
        session.get_one(Document, uuid.UUID(ids[1])).status = "processing"
    assert len(storage.files) == 2  # The snapshot is deleted too.

    many = client.post("/api/v1/documents/delete", json={"ids": ids[1:]}).json()
    assert many["deleted"] == 1 and "正在处理" in many["skipped"][0]
    assert client.get("/api/v1/documents").json()["total"] == 1
    actions = [x["action"] for x in client.get("/api/v1/audit-logs").json()["items"]]
    assert actions.count("document:delete") == 2
