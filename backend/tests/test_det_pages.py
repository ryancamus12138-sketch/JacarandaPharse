"""Detection samples: pre-annotation, box adjustment, confirmation, export, storage in the dataset
library."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import pytest

from jacara.core.db import session_scope
from jacara.models import AuditLog, DetPage, Document, OcrSample, OcrSampleExport
from jacara.modules.hub import datasets as dataset_files
from jacara.pipeline.store import save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator

from .test_ocr_samples import KEY, page_png


@pytest.fixture
def document_id(factory, receipt_page, storage, tmp_path) -> uuid.UUID:
    """A reviewed receipt (photo) whose OCR result is receipt_page from conftest (15 text
    blocks)."""
    fields = (F("total", "number", 9.0, 9),)
    result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
    report = DocumentReport(
        source="receipt.png",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator().validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 14),
        ocr_page=receipt_page,
    )
    path = tmp_path / "receipt.png"
    path.write_bytes(b"x")
    storage.put(KEY, page_png(), "image/png")
    with session_scope(factory) as session:
        document = save_document(session, path, [report])
        document.storage_key = KEY
        document.content_type = "image/png"
        document.status = "reviewed"
        document.reviewed_at = datetime.now(UTC)
        return document.id


def url(document_id, *rest: str) -> str:
    return "/".join([f"/api/v1/ocr-samples/det-pages/{document_id}/1", *rest])


def test_prefill_uses_ocr_boxes_and_corrected_samples(client, factory, document_id):
    with session_scope(factory) as session:
        # During review the date block was corrected and a region missed by OCR was selected.
        common = {
            "document_id": document_id,
            "page": 1,
            "unit_page": 1,
            "template": "",
            "field": "",
            "source_format": "image",
            "status": "ready",
            "user_id": uuid.UUID(int=1),
            "team_id": uuid.UUID(int=1),
        }
        session.add(
            OcrSample(
                kind="line",
                polygon=[[163, 369], [345, 369], [345, 390], [163, 390]],
                transcription="25/12/2018 8:18:39 PM",
                ocr_text="25/12/2018 8:13:39 PM",
                category="rec_error",
                **common,
            )
        )
        session.add(
            OcrSample(
                kind="box",
                polygon=[[60, 900], [120, 900], [120, 920], [60, 920]],
                transcription="THANK YOU",
                ocr_text=None,
                category="missed",
                **common,
            )
        )
    page = client.get(url(document_id)).json()
    assert (page["status"], page["revision"], page["width"], page["height"]) == (
        "none",
        0,
        500,
        1000,
    )
    boxes = page["boxes"]
    assert len(boxes) == 16  # 15 OCR text blocks + 1 box for missed text.
    date = next(b for b in boxes if b["origin"] == "sample")
    assert date["text"] == "25/12/2018 8:18:39 PM"
    assert boxes[-1] == {
        "points": [[60.0, 900.0], [120.0, 900.0], [120.0, 920.0], [60.0, 920.0]],
        "text": "THANK YOU",
        "ignore": False,
        "origin": "missed",
    }
    listed = client.get("/api/v1/ocr-samples/det-pages").json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "none"


def test_save_confirm_and_revision_conflict(client, factory, document_id):
    boxes = client.get(url(document_id)).json()["boxes"]
    boxes[-1]["ignore"] = True  # The last box is illegible.
    boxes.append(
        {"points": [[10, 10], [60, 10], [60, 30], [10, 30]], "text": "LOGO", "origin": "manual"}
    )
    saved = client.put(url(document_id), json={"revision": 0, "boxes": boxes})
    assert saved.status_code == 200, saved.text
    assert (saved.json()["status"], saved.json()["revision"]) == ("draft", 1)
    stale = client.put(url(document_id), json={"revision": 0, "boxes": boxes})
    assert stale.status_code == 409
    confirmed = client.post(url(document_id, "confirm"), json={"revision": 1, "boxes": boxes})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    with session_scope(factory) as session:
        page = session.query(DetPage).one()
        assert (page.revision, page.confirmed_at is not None) == (2, True)
        assert page.boxes[-2]["text"] == "" and page.boxes[-2]["ignore"]
        actions = {log.action for log in session.query(AuditLog)}
    assert {"ocr_samples:det_save", "ocr_samples:det_confirm"} <= actions


def test_bad_boxes_are_refused(client, document_id):
    crossed = [{"points": [[0, 0], [50, 20], [50, 0], [0, 20]], "text": "X"}]
    response = client.put(url(document_id), json={"revision": 0, "boxes": crossed})
    assert response.status_code == 422 and "四个角不对" in response.json()["detail"]
    empty = [{"points": [[0, 0], [50, 0], [50, 20], [0, 20]], "text": " "}]
    response = client.put(url(document_id), json={"revision": 0, "boxes": empty})
    assert "没有文字" in response.json()["detail"]
    blurry = [{"points": [[0, 0], [50, 0], [50, 20], [0, 20]], "text": "", "ignore": True}]
    response = client.post(url(document_id, "confirm"), json={"revision": 0, "boxes": blurry})
    assert "一个能用的框都没有" in response.json()["detail"]


def test_unreviewed_documents_are_not_offered(client, factory, document_id):
    with session_scope(factory) as session:
        session.get_one(Document, document_id).status = "done"
    assert client.get("/api/v1/ocr-samples/det-pages").json()["total"] == 0
    assert client.get(url(document_id)).status_code == 422


def test_export_and_save_to_dataset(client, factory, document_id):
    boxes = client.get(url(document_id)).json()["boxes"]
    boxes[0]["ignore"] = True
    assert (
        client.post(url(document_id, "confirm"), json={"revision": 0, "boxes": boxes}).status_code
        == 200
    )
    preview = client.get("/api/v1/ocr-samples/det-pages/export/preview").json()
    assert preview["included"] == 1 and preview["boxes"] == 15
    response = client.get("/api/v1/ocr-samples/det-pages/export")
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        label_file = next(n for n in names if n.startswith("det_gt_"))
        path, _, raw = archive.read(label_file).decode("utf-8").strip().partition("\t")
        manifest = json.loads(archive.read("manifest.jsonl"))
    assert path == "images/p_0001.png" and path in names and "README.md" in names
    label = json.loads(raw)
    assert label[0]["transcription"] == "###"
    assert label[4] == {
        "transcription": "25/12/2018 8:13:39 PM",
        "points": [[163, 369], [345, 369], [345, 390], [163, 390]],
    }
    assert manifest["document_group"].startswith("g_") and manifest["ignored"] == 1
    # The export is a detection dataset ready for training.
    parsed = dataset_files.parse(response.content, "ocr_det")
    assert sum(parsed.counts.values()) == 1 and parsed.items[0].boxes[0].ignore

    saved = client.post(
        "/api/v1/ocr-samples/det-pages/export/dataset",
        json={"name": "收据检测", "description": "第一批"},
    )
    assert saved.status_code == 201, saved.text
    assert (saved.json()["kind"], saved.json()["source"]) == ("ocr_det", "det_pages")
    with session_scope(factory) as session:
        records = session.query(OcrSampleExport).all()
        assert [r.kind for r in records] == ["det", "det"]
        assert records[0].items[0]["det_page"]


def test_deleting_the_document_deletes_its_det_pages(client, factory, document_id):
    boxes = client.get(url(document_id)).json()["boxes"]
    assert client.put(url(document_id), json={"revision": 0, "boxes": boxes}).status_code == 200
    assert client.delete(f"/api/v1/documents/{document_id}").status_code == 200
    with session_scope(factory) as session:
        assert session.query(DetPage).count() == 0
