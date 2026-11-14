"""OCR correction samples: switch, collection during review, cropping, classification, samples page,
ZIP export, deletion with the document.

Documents use receipt_page from conftest (a 500 × 1000 receipt where OCR reads the change as
100); the image is a PNG of the same size."""

import io
import json
import uuid
import zipfile
from datetime import datetime

import numpy as np
import pytest
from PIL import Image, ImageDraw

from jacara.core.db import session_scope
from jacara.models import AuditLog, Document, Extraction, OcrSample
from jacara.modules.ocr_samples.crop import (
    category_of,
    clean,
    crop_polygon,
    error_tags,
    png_bytes,
)
from jacara.pipeline.store import save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator
from jacara.templates import load_template

DATE_LINE = 4  # "25/12/2018 8:13:39 PM", box (163, 369)-(345, 390)
CHANGE_LINE = 13  # "100", box (407, 768)-(447, 791)
KEY = "documents/receipt.png"
RECEIPT = load_template("receipt").title


def page_png() -> bytes:
    image = Image.new("RGB", (500, 1000), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((170, 372, 340, 386), fill="black")  # Text of the date line.
    draw.rectangle((410, 770, 440, 788), fill="black")  # Change.
    return png_bytes(image)


@pytest.fixture
def document_id(factory, receipt_page, storage, tmp_path) -> uuid.UUID:
    fields = (
        F("merchant", "string", "BOOK TA _K (TAMAN DAYA) SDN BHD", 1),
        F("date", "date", None, None),
        F("total", "number", 9.0, 6),
        F("change", "number", 100.0, 13),
    )
    result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
    report = DocumentReport(
        source="receipt.png",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator().validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 13),
        ocr_page=receipt_page,
    )
    path = tmp_path / "receipt.png"
    path.write_bytes(b"x")
    storage.put(KEY, page_png(), "image/png")
    with session_scope(factory) as session:
        document = save_document(session, path, [report])
        document.storage_key = KEY
        document.content_type = "image/png"
        return document.id


def values(client, document_id) -> dict[str, dict]:
    extraction = client.get(f"/api/v1/documents/{document_id}").json()["extractions"][0]
    return {v["name"]: v for v in extraction["values"]}


def turn_on(client, on: bool = True) -> None:
    response = client.put("/api/v1/ocr-samples/settings", json={"collect": on})
    assert response.status_code == 200
    assert response.json()["collect"] is on


def pick(client, field: dict, line: int, transcription: str, **extra):
    body = {"action": "edit", "source_lines": [line], "transcription": transcription, **extra}
    return client.post(f"/api/v1/values/{field['id']}/revisions", json=body)


def draw(client, field: dict, box: list[float], text: str):
    body = {"action": "edit", "value": text, "source_box": box, "transcription": text}
    return client.post(f"/api/v1/values/{field['id']}/revisions", json=body)


def samples(client, **params) -> dict:
    response = client.get("/api/v1/ocr-samples", params=params)
    assert response.status_code == 200
    return response.json()


# ---- Switch ----


def test_off_by_default_saves_the_review_but_no_sample(client, document_id):
    assert client.get("/api/v1/ocr-samples/settings").json() == {
        "collect": False,
        "can_change": True,
    }
    response = pick(client, values(client, document_id)["date"], DATE_LINE, "25/12/2018 8:18:39 PM")
    assert response.status_code == 201
    # The value is parsed from the corrected transcription.
    assert response.json()["current_value"] == "2018-12-25"
    assert samples(client)["total"] == 0


def test_switch_is_recorded(client, factory, document_id):
    turn_on(client)
    turn_on(client, False)
    with session_scope(factory) as session:
        logs = session.query(AuditLog).filter_by(action="ocr_samples:settings").all()
        assert [log.detail["collect"] for log in logs] == [True, False]


# ---- Collection ----


def test_corrected_line_becomes_a_rec_error_sample(client, storage, document_id):
    turn_on(client)
    date = values(client, document_id)["date"]
    assert pick(client, date, DATE_LINE, " 25/12/2018 8:18:39 PM ").status_code == 201
    listed = samples(client)
    assert listed["total"] == 1
    sample = listed["items"][0]
    assert sample["kind"] == "line"
    assert sample["transcription"] == "25/12/2018 8:18:39 PM"  # Surrounding whitespace stripped.
    assert sample["ocr_text"] == "25/12/2018 8:13:39 PM"
    assert sample["category"] == "rec_error"
    assert sample["error_tags"] == ["substitution"]
    assert sample["field"] == "date"
    assert sample["source_format"] == "image"
    assert sample["ocr_engine"] == "rapidocr test"
    assert sample["status"] == "ready"
    # Cropped by the block's four corners: 182 × 21, the same size the recognition model saw.
    assert (sample["width"], sample["height"]) == (182, 21)
    image = client.get(f"/api/v1/ocr-samples/{sample['id']}/image")
    assert image.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(image.content)).size == (182, 21)
    assert listed["categories"] == {"rec_error": 1}


def test_unchanged_line_is_a_correct_sample(client, document_id):
    turn_on(client)
    total = values(client, document_id)["total"]
    assert pick(client, total, 9, "9.00").status_code == 201
    sample = samples(client)["items"][0]
    assert (sample["category"], sample["error_tags"]) == ("correct", [])


def test_the_audit_log_points_to_the_sample(client, factory, document_id):
    turn_on(client)
    pick(client, values(client, document_id)["change"], CHANGE_LINE, "1.00")
    sample_id = samples(client)["items"][0]["id"]
    with session_scope(factory) as session:
        log = session.query(AuditLog).filter_by(action="value:edit").one()
        assert log.detail["ocr_sample"] == sample_id


def test_drawn_box_without_ocr_text_is_missed(client, document_id):
    turn_on(client)
    merchant = values(client, document_id)["merchant"]
    assert draw(client, merchant, [10, 940, 120, 960], "THANK YOU").status_code == 201
    sample = samples(client)["items"][0]
    assert (sample["kind"], sample["category"], sample["ocr_text"]) == ("box", "missed", None)
    assert sample["error_tags"] == []


def test_drawn_box_over_ocr_text_is_unclear(client, document_id):
    turn_on(client)
    change = values(client, document_id)["change"]
    assert draw(client, change, [400, 760, 450, 795], "1.00").status_code == 201
    sample = samples(client)["items"][0]
    assert (sample["category"], sample["ocr_text"]) == ("unclear", "100")
    assert sample["error_tags"] == ["missing"]  # "100" → "1.00": missing decimal point.
    # Padding around the region (as with region re-recognition).
    assert sample["width"] > 50 and sample["height"] > 35


def test_transcription_needs_exactly_one_block(client, document_id):
    turn_on(client)
    date = values(client, document_id)["date"]
    body = {"action": "edit", "source_lines": [3, 4], "transcription": "Date: 25/12/2018"}
    response = client.post(f"/api/v1/values/{date['id']}/revisions", json=body)
    assert response.status_code == 422
    confirm = {"action": "confirm", "transcription": "x"}
    assert client.post(f"/api/v1/values/{date['id']}/revisions", json=confirm).status_code == 422
    assert samples(client)["total"] == 0


@pytest.mark.parametrize("text", ["   ", "a\tb"])
def test_unusable_transcription_saves_the_review_only(client, document_id, text):
    turn_on(client)
    merchant = values(client, document_id)["merchant"]
    response = pick(client, merchant, 0, text, value="BOOK TA K")
    assert response.status_code == 201
    assert samples(client)["total"] == 0


def test_sensitive_fields_are_not_collected(client, document_id, monkeypatch):
    from jacara.modules.ocr_samples import service

    monkeypatch.setattr(service, "_sensitive", lambda template, field: True)
    turn_on(client)
    response = pick(client, values(client, document_id)["change"], CHANGE_LINE, "1.00")
    assert response.status_code == 201
    assert samples(client)["total"] == 0


def test_pdf_text_layer_is_not_collected(client, factory, document_id):
    with session_scope(factory) as session:
        for extraction in session.query(Extraction):
            extraction.source = "pdf-text"
    turn_on(client)
    pick(client, values(client, document_id)["change"], CHANGE_LINE, "1.00")
    assert samples(client)["total"] == 0


def test_failed_crop_keeps_the_review_and_can_be_retried(client, storage, document_id):
    turn_on(client)
    data = storage.files.pop(KEY)
    response = pick(client, values(client, document_id)["change"], CHANGE_LINE, "1.00")
    assert response.status_code == 201
    assert response.json()["current_value"] == "1.0"
    sample = samples(client)["items"][0]
    assert (sample["status"], sample["error"]) == ("failed", "对象存储暂时不可用")
    assert client.post(f"/api/v1/ocr-samples/{sample['id']}/retry").status_code == 503
    storage.files[KEY] = data
    retried = client.post(f"/api/v1/ocr-samples/{sample['id']}/retry").json()
    assert (retried["status"], retried["width"], retried["height"]) == ("ready", 40, 23)


# ---- Samples page ----


def test_edit_withdraw_and_restore(client, factory, document_id):
    turn_on(client)
    pick(client, values(client, document_id)["change"], CHANGE_LINE, "1.00")
    sample_id = samples(client)["items"][0]["id"]
    url = f"/api/v1/ocr-samples/{sample_id}"
    edited = client.patch(url, json={"transcription": "100"}).json()
    # Reclassified accordingly.
    assert (edited["category"], edited["error_tags"]) == ("correct", [])
    assert client.patch(url, json={"transcription": "1\n00"}).status_code == 422
    assert client.patch(url, json={"status": "withdrawn"}).json()["status"] == "withdrawn"
    assert samples(client, status="withdrawn")["total"] == 1
    assert samples(client)["categories"] == {}  # Withdrawn samples do not count.
    restored = client.patch(url, json={"status": "ready", "category": "unclear"}).json()
    assert (restored["status"], restored["category"]) == ("ready", "unclear")
    with session_scope(factory) as session:
        actions = [
            log.detail for log in session.query(AuditLog).filter_by(action="ocr_samples:update")
        ]
        assert actions[0]["transcription"] == ["1.00", "100"]
        assert len(actions) == 3


def test_filters(client, document_id):
    turn_on(client)
    pick(client, values(client, document_id)["change"], CHANGE_LINE, "1.00")
    pick(client, values(client, document_id)["total"], 9, "9.00")
    assert samples(client, category="correct")["total"] == 1
    assert samples(client, category=["correct", "rec_error"])["total"] == 2
    assert samples(client, source_format="pdf")["total"] == 0
    assert client.get("/api/v1/ocr-samples/templates").json() == [
        {"key": "receipt", "title": RECEIPT}
    ]
    assert samples(client, template="receipt")["total"] == 2
    assert samples(client, template="other")["total"] == 0


def test_a_sample_is_one_template_and_field_in_every_language(client, document_id, monkeypatch):
    from dataclasses import replace

    from jacara.modules.ocr_samples import service as samples_service
    from jacara.pipeline import current

    base = load_template("receipt")
    bilingual = replace(
        base,
        titles=(("en", "Receipt"),),
        fields=tuple(
            replace(f, names=(("en", "Total"),)) if f.name == "total" else f for f in base.fields
        ),
    )
    monkeypatch.setattr(samples_service, "template_of", lambda extraction: bilingual)
    monkeypatch.setattr(current, "template_of", lambda extraction: bilingual)
    turn_on(client)
    pick(client, values(client, document_id)["total"], 9, "9.00")
    english = {"Accept-Language": "en"}
    zh = samples(client)["items"][0]
    en = client.get("/api/v1/ocr-samples", headers=english).json()["items"][0]
    label = next(f.label for f in base.fields if f.name == "total")
    assert (zh["template"], zh["field_label"]) == (RECEIPT, label)
    assert (en["template"], en["field_label"]) == ("Receipt", "Total")
    # Shown in two languages, still one template and one field.
    assert zh["template_key"] == en["template_key"] == "receipt"
    assert zh["field"] == en["field"] == "total"
    assert client.get("/api/v1/ocr-samples/templates", headers=english).json() == [
        {"key": "receipt", "title": "Receipt"}
    ]
    manifests = []
    for headers in ({}, english):
        archive = zipfile.ZipFile(
            io.BytesIO(client.get("/api/v1/ocr-samples/export", headers=headers).content)
        )
        manifests.append(json.loads(archive.read("manifest.jsonl").decode().splitlines()[0]))
    chinese, translated = manifests
    assert (chinese["template_id"], chinese["field"]) == (translated["template_id"], "total")
    assert (chinese["template"], translated["template"]) == (RECEIPT, "Receipt")
    assert (chinese["field_label"], translated["field_label"]) == (label, "Total")


# ---- Export ----


def test_export_preview_and_zip(client, factory, document_id):
    turn_on(client)
    v = values(client, document_id)
    pick(client, v["change"], CHANGE_LINE, "1.00")  # Exported.
    pick(client, v["total"], 9, "9.00")  # Correct line, exported.
    # Two transcriptions of the same image ↓
    pick(client, v["date"], DATE_LINE, "25/12/2018 8:18:39 PM")
    pick(client, v["date"], DATE_LINE, "25/12/2018 8:19:39 PM")
    pick(client, v["merchant"], 0, "BOOK TA.K (TAMAN DAYA) SDN BHD")  # Withdrawn ↓
    withdrawn = next(s for s in samples(client)["items"] if s["field"] == "merchant")
    client.patch(f"/api/v1/ocr-samples/{withdrawn['id']}", json={"status": "withdrawn"})

    preview = client.get("/api/v1/ocr-samples/export/preview").json()
    assert preview["included"] == 2
    assert preview["categories"] == {"rec_error": 1, "correct": 1}
    assert {e["reason"]: e["count"] for e in preview["excluded"]} == {
        "同一张图有两种写法": 2,
        "撤回了": 1,
    }
    only = client.get("/api/v1/ocr-samples/export/preview", params={"category": "correct"})
    assert only.json()["included"] == 1

    response = client.get("/api/v1/ocr-samples/export")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    # One document, one group: the whole group goes to training or validation by hash.
    part = next(p for p in ("train", "val") if f"rec_gt_{p}.txt" in names)
    assert names == {
        "crop_img/s_0001.png",
        "crop_img/s_0002.png",
        f"rec_gt_{part}.txt",
        "rec_gt.txt",
        "manifest.jsonl",
        "README.md",
        "corpus/real_text.txt",
        "corpus/hard_chars.txt",
    }
    assert archive.read(f"rec_gt_{part}.txt") == archive.read("rec_gt.txt")
    labels = archive.read("rec_gt.txt").decode("utf-8")
    assert not labels.startswith("﻿")
    assert labels.splitlines() == ["crop_img/s_0001.png\t1.00", "crop_img/s_0002.png\t9.00"]
    rows = [json.loads(r) for r in archive.read("manifest.jsonl").decode().splitlines()]
    group = rows[0].pop("document_group")
    assert group.startswith("g_") and len(group) == 14  # Stable group id without business data.
    assert rows[0] == {
        "sample_id": "s_0001",
        "image": "crop_img/s_0001.png",
        "transcription": "1.00",
        "ocr_text": "100",
        "category": "rec_error",
        "error_tags": ["missing"],
        "template_id": "receipt",
        "template": RECEIPT,
        "field": "change",
        "field_label": "change",  # Not a field of the template: its ID is the name.
        "split": part,
        "split_version": 1,
        "sample_revision": 1,
        "source_format": "image",
        "ocr_model_version": "rapidocr test",
    }
    assert rows[1]["document_group"] == group  # Same document.
    for row in rows:  # Every path resolves to an image that opens.
        Image.open(io.BytesIO(archive.read(row["image"]))).verify()
    readme = archive.read("README.md").decode("utf-8")
    assert "同一张图有两种写法：2 条" in readme and "label_file_list" in readme
    everything = b"".join(archive.read(n) for n in names)
    assert b"receipt.png" not in everything  # Real file names are not included.
    with session_scope(factory) as session:
        log = session.query(AuditLog).filter_by(action="ocr_samples:export").one()
        assert log.detail["count"] == 2


def test_same_image_same_text_is_exported_once(client, document_id):
    turn_on(client)
    v = values(client, document_id)
    pick(client, v["change"], CHANGE_LINE, "1.00")
    pick(client, v["change"], CHANGE_LINE, "1.00")
    preview = client.get("/api/v1/ocr-samples/export/preview").json()
    assert preview["included"] == 1
    assert preview["excluded"] == [{"reason": "同一张图重复，只留一份", "count": 1}]


def test_nothing_to_export(client, document_id):
    response = client.get("/api/v1/ocr-samples/export")
    assert response.status_code == 422


# ---- Document deletion ----


def test_deleting_the_document_deletes_its_samples(client, factory, storage, document_id):
    turn_on(client)
    pick(client, values(client, document_id)["change"], CHANGE_LINE, "1.00")
    with session_scope(factory) as session:
        crop = session.query(OcrSample).one().crop_key
    assert crop in storage.files
    response = client.post("/api/v1/documents/delete", json={"ids": [str(document_id)]})
    assert response.status_code == 200, response.text
    with session_scope(factory) as session:
        assert session.query(OcrSample).count() == 0
        assert session.query(Document).count() == 0
    assert crop not in storage.files and KEY not in storage.files


# ---- Cropping and classification (no database) ----


def test_vertical_lines_are_turned():
    image = Image.new("RGB", (100, 200), "white")
    crop = crop_polygon(png_bytes(image), [[10, 10], [30, 10], [30, 110], [10, 110]])
    assert crop.size == (100, 20)


def test_tilted_line_is_straightened():
    image = Image.new("RGB", (200, 200), "black")
    crop = crop_polygon(png_bytes(image), [[20, 40], [120, 20], [124, 40], [24, 60]])
    assert crop.size[0] > crop.size[1]
    assert np.array(crop).mean() == 0


@pytest.mark.parametrize(
    ("ocr", "truth", "tags"),
    [
        ("17/O5/24", "17/05/24", ["substitution"]),
        ("100", "1.00", ["missing"]),
        ("9.000", "9.00", ["extra"]),
        ("Total  9.00", "TOTAL 9.00", ["format"]),
        ("ＡＢＣ", "ABC", ["format"]),  # Full-width and half-width.
        ("", "9.00", ["empty"]),
        ("9.00", "9.00", []),
        (None, "9.00", []),
        ("1OO", "100", ["substitution"]),
    ],
)
def test_error_tags(ocr, truth, tags):
    assert error_tags(ocr, truth) == tags


def test_categories():
    assert category_of("line", "a", "a") == "correct"
    assert category_of("line", "a", "b") == "rec_error"
    assert category_of("box", None, "b") == "missed"
    assert category_of("box", "a", "a") == "unclear"


def test_clean():
    assert clean("  17/05/24 ") == "17/05/24"
    assert clean("a  b") == "a  b"  # Inner spaces are kept.
    assert clean("") is None
    assert clean("a\nb") is None


# ---- Backfilling from earlier corrections ----


def test_backfill_collects_old_edits_as_pending(client, factory, document_id):
    """Reviews made while the switch was off: corrections made by clicking source text or selecting
    a region are backfilled as pending; typed values are not collected; no duplicates."""
    fields = values(client, document_id)
    old = {"action": "edit", "source_lines": [DATE_LINE]}
    assert (
        client.post(f"/api/v1/values/{fields['date']['id']}/revisions", json=old).status_code == 201
    )
    box = {"action": "edit", "value": "1.00", "source_box": [405, 765, 450, 795]}
    assert (
        client.post(f"/api/v1/values/{fields['change']['id']}/revisions", json=box).status_code
        == 201
    )
    typed = {"action": "edit", "value": "9.5", "reason": "手打"}
    assert (
        client.post(f"/api/v1/values/{fields['total']['id']}/revisions", json=typed).status_code
        == 201
    )
    assert samples(client)["total"] == 0  # Switch off: nothing was collected at the time.

    result = client.post("/api/v1/ocr-samples/backfill").json()
    assert result["created"] == 2
    assert any("手打" in s["reason"] for s in result["skipped"])
    pending = samples(client, status="pending")["items"]
    assert {s["kind"] for s in pending} == {"line", "box"}
    assert all(s["origin"] == "backfill" for s in pending)
    line = next(s for s in pending if s["kind"] == "line")
    # Initial guess from the OCR reading; corrected during verification.
    assert line["transcription"] == line["ocr_text"]
    box_sample = next(s for s in pending if s["kind"] == "box")
    # The stored value is normalized; the reviewer writes the transcription during verification.
    assert box_sample["reviewed_value"] == "1.0"

    again = client.post("/api/v1/ocr-samples/backfill").json()
    assert again["created"] == 0  # Each correction is collected once.

    # Unverified samples cannot be exported.
    preview = client.get("/api/v1/ocr-samples/export/preview").json()
    assert any("还没复核" in e["reason"] for e in preview["excluded"])

    # Verification: correct to the actual text on the image and approve.
    fixed = client.patch(
        f"/api/v1/ocr-samples/{line['id']}",
        json={"transcription": "25/12/2018 8:13:39 PM", "status": "ready"},
    ).json()
    assert fixed["status"] == "ready"
    with session_scope(factory) as session:
        actions = [a.action for a in session.query(AuditLog).all()]
    assert "ocr_samples:backfill" in actions and "ocr_samples:verify" in actions


# ---- Correct lines, bulk approval, corpus for synthesis ----


def test_confirmed_lines_are_collected_for_review(client, factory, document_id):
    """Reviewed, uncorrected, with evidence exactly one whole block containing only the value:
    collected as a correctly read line, pending until a person has looked at it."""
    from jacara.modules.ocr_samples.service import only_value

    assert only_value("number", "9.0", "9.00") and only_value("number", "9.0", "RM 9.00")
    assert not only_value("number", "9.0", "Total : 9.00")
    assert only_value("date", "2018-12-25", "25/12/2018")
    assert not only_value("string", "SHOP", "SHOP SDN BHD")

    with session_scope(factory) as session:
        session.get_one(Document, document_id).status = "reviewed"
    result = client.post("/api/v1/ocr-samples/confirmed-lines").json()
    pending = samples(client, status="pending")["items"]
    assert result["created"] == len(pending) >= 2
    assert {s["origin"] for s in pending} == {"confirmed"}
    assert all(s["category"] == "correct" and s["transcription"] == s["ocr_text"] for s in pending)
    # No duplicates.
    assert client.post("/api/v1/ocr-samples/confirmed-lines").json()["created"] == 0

    preview = client.get("/api/v1/ocr-samples/export/preview").json()
    assert preview["included"] == 0 and preview["paddle_minimum"] == 5000

    ids = [s["id"] for s in pending]
    assert client.post("/api/v1/ocr-samples/verify", json={"ids": ids}).json() == {
        "verified": len(ids)
    }
    assert client.post("/api/v1/ocr-samples/verify", json={"ids": ids}).json()["verified"] == 0
    response = client.get("/api/v1/ocr-samples/export")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        texts = archive.read("corpus/real_text.txt").decode("utf-8").splitlines()
        readme = archive.read("README.md").decode("utf-8")
    assert "9.00" in texts
    assert "至少 5000 行" in readme and "10:1 到 5:1" in readme


def test_hard_characters_come_from_misread_samples():
    from types import SimpleNamespace

    from jacara.modules.ocr_samples.service import corpus

    wrong = SimpleNamespace(category="rec_error", ocr_text="100", transcription="1.00")
    right = SimpleNamespace(category="correct", ocr_text="9.00", transcription="9.00")
    texts, hard = corpus([wrong, right])  # type: ignore[list-item]
    assert texts == ["1.00", "9.00"]
    assert hard == {".": 1}


# ---- Storing in the dataset library ----


def test_export_straight_into_the_dataset_library(client, factory, document_id):
    from jacara.models import Dataset, OcrSampleExport

    turn_on(client)
    assert (
        pick(
            client, values(client, document_id)["date"], DATE_LINE, "25/12/2018 8:18:39 PM"
        ).status_code
        == 201
    )
    response = client.post("/api/v1/ocr-samples/export/dataset", json={"name": "收据识别"})
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert (dataset["kind"], dataset["source"], dataset["version"]) == ("ocr_rec", "samples", 1)
    assert sum(dataset["counts"].values()) == 1
    with session_scope(factory) as session:
        record = session.query(OcrSampleExport).one()  # An export record is written as usual.
        assert session.query(Dataset).one().export_id == record.id
