"""Grouping OCR samples by business document and splitting into training and validation sets.

Acceptance cases: a document reprocessed several times, a PDF and a photo, several documents in
one file, duplicates found after export; repeated exports of the same data have identical
members; training and validation groups are disjoint; the locked test set is excluded from
regular exports and never used as a source of examples."""

import io
import uuid
import zipfile
from datetime import datetime

import pytest

from jacara.core.db import session_scope
from jacara.core.i18n import render
from jacara.models import DocGroupMember, Document, OcrSample, OcrSampleExport
from jacara.modules.ocr_samples import groups
from jacara.pipeline.store import add_extractions, save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator

from .test_ocr_samples import CHANGE_LINE

TOTAL_LINE = 9  # "9.00"


def report(receipt_page, page: int = 1, merchant: str = "BOOK TA _K (TAMAN DAYA) SDN BHD"):
    fields = (
        F("merchant", "string", merchant, 1),
        F("date", "date", "2018-12-25", 4),
        F("total", "number", 9.0, 6),
        F("change", "number", 100.0, 13),
    )
    result = ExtractionResult(fields, "{}", "test-model", "fake", "extract-v4", 5)
    return DocumentReport(
        source="r",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator().validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 13),
        ocr_page=receipt_page,
        page=page,
    )


def page_png(content: bytes) -> bytes:
    """Each file has a different image (crop hashes differ); files with identical bytes have
    identical images."""
    import hashlib

    from PIL import Image, ImageDraw

    from jacara.modules.ocr_samples.crop import png_bytes

    digest = hashlib.sha256(content).digest()
    image = Image.new("RGB", (500, 1000), "white")
    draw = ImageDraw.Draw(image)
    for box in ((170, 372, 340, 386), (410, 770, 440, 788), (410, 638, 440, 652)):
        draw.rectangle(box, fill=(digest[0], digest[1], digest[2]))
    return png_bytes(image)


@pytest.fixture
def make(factory, storage, receipt_page, tmp_path):
    """Create a processed document. content: file bytes (identical bytes = duplicate upload); pages:
    first page of each document."""

    def build(name: str, content: bytes, pages=(1,), **kw) -> uuid.UUID:
        path = tmp_path / name
        path.write_bytes(content)
        key = f"documents/{name}"
        storage.put(key, page_png(content), "image/png")
        reports = [report(receipt_page, page=p, **kw) for p in pages]
        with session_scope(factory) as session:
            document = save_document(session, path, reports)
            document.storage_key = key
            document.content_type = "image/png"  # All crops come from that PNG.
            document.page_count = max(pages)
            return document.id

    return build


def turn_on(client):
    assert client.put("/api/v1/ocr-samples/settings", json={"collect": True}).status_code == 200


def values(client, document_id, page: int = 1) -> dict[str, dict]:
    detail = client.get(f"/api/v1/documents/{document_id}").json()
    extraction = next(e for e in detail["extractions"] if e["page"] == page)
    return {v["name"]: v for v in extraction["values"]}


def collect(client, document_id, page: int = 1, field="change", line=CHANGE_LINE, text="1.00"):
    value = values(client, document_id, page)[field]
    body = {"action": "edit", "source_lines": [line], "transcription": text, "page": page}
    response = client.post(f"/api/v1/values/{value['id']}/revisions", json=body)
    assert response.status_code == 201, response.text


def group_of(factory, document_id, page: int = 1) -> uuid.UUID:
    with session_scope(factory) as session:
        return groups.member_of(session, (document_id, page)).group_id  # type: ignore[union-attr]


def set_split(client, group_id, split):
    return client.put(f"/api/v1/ocr-samples/groups/{group_id}/split", json={"split": split})


def preview(client, **params) -> dict:
    return client.get("/api/v1/ocr-samples/export/preview", params=params).json()


def export(client, **params) -> zipfile.ZipFile:
    response = client.get("/api/v1/ocr-samples/export", params=params)
    assert response.status_code == 200, response.text
    return zipfile.ZipFile(io.BytesIO(response.content))


# ---- Splits ----


def test_hash_split_is_stable_and_about_one_in_five():
    ids = [uuid.UUID(int=n) for n in range(2000)]
    first = [groups.hashed_split("seed", 1, g) for g in ids]
    assert first == [groups.hashed_split("seed", 1, g) for g in ids]  # Same input, same result.
    share = first.count("val") / len(first)
    assert 0.17 < share < 0.23
    # Reassigned with a new version.
    assert first != [groups.hashed_split("seed", 2, g) for g in ids]


def test_first_sample_assigns_a_group_and_a_stored_split(client, factory, make):
    turn_on(client)
    doc = make("a.png", b"a")
    collect(client, doc)
    group = group_of(factory, doc)
    with session_scope(factory) as session:
        team = session.get_one(Document, doc).team_id
        stored = groups.assignment(session, team, group)
        assert stored is not None and stored.split in ("train", "val")
        assert render(stored.reason) == "首次进入数据集时按哈希分配"


# ---- What counts as the same document ----


def test_rerun_stays_in_the_same_group(client, factory, make, receipt_page):
    """Reprocess once: a new extraction for the same business document."""
    turn_on(client)
    doc = make("a.png", b"a")
    collect(client, doc)
    with session_scope(factory) as session:
        document = session.get_one(Document, doc)
        add_extractions(session, document, [report(receipt_page)], run=2)
        document.current_run = 2
    collect(client, doc, field="total", line=TOTAL_LINE, text="9.00")
    with session_scope(factory) as session:
        assert session.query(DocGroupMember).count() == 1
        assert {s.unit_page for s in session.query(OcrSample)} == {1}


def test_same_bytes_share_a_group(client, factory, make):
    turn_on(client)
    first, again = make("a.png", b"same"), make("a (1).png", b"same")
    collect(client, first)
    collect(client, again)
    assert group_of(factory, first) == group_of(factory, again)
    # Grouped automatically, without confirmation.
    assert client.get("/api/v1/ocr-samples/pairs").json() == []


def test_two_documents_in_one_file_are_two_groups(client, factory, make):
    turn_on(client)
    doc = make("two.pdf", b"two", pages=(1, 2))
    collect(client, doc, page=1)
    collect(client, doc, page=2)
    assert group_of(factory, doc, 1) != group_of(factory, doc, 2)


# ---- Pairing PDFs and photos ----


def test_pdf_and_photo_are_nominated_and_merged_after_a_person_confirms(client, factory, make):
    turn_on(client)
    pdf, photo = make("a.pdf", b"pdf"), make("a.jpg", b"photo")
    collect(client, pdf)
    [pair] = client.get("/api/v1/ocr-samples/pairs").json()
    assert {pair["a"]["document_id"], pair["b"]["document_id"]} == {str(pdf), str(photo)}
    assert pair["a"]["samples"] == 1  # The one with samples first.
    assert any("9.0" in v for v in pair["a"]["values"].values())  # Values compared side by side.
    body = {
        "a": {"document_id": str(pdf), "page": 1},
        "b": {"document_id": str(photo), "page": 1},
        "decision": "same",
    }
    result = client.post("/api/v1/ocr-samples/pairs", json=body).json()
    assert not result["quarantined"]
    collect(client, photo)  # Later samples of the photo join the same group.
    assert group_of(factory, pdf) == group_of(factory, photo)
    assert client.get("/api/v1/ocr-samples/pairs").json() == []
    [group] = client.get("/api/v1/ocr-samples/groups").json()["items"]
    assert len(group["members"]) == 2 and group["samples"] == 2


def test_only_date_and_amount_are_not_enough(client, make):
    turn_on(client)
    a, _ = make("a.pdf", b"a"), make("b.jpg", b"b", merchant="ANOTHER SHOP")
    collect(client, a)
    assert client.get("/api/v1/ocr-samples/pairs").json() == []  # Different issuer.


def test_different_is_remembered(client, make):
    turn_on(client)
    pdf, photo = make("a.pdf", b"pdf"), make("a.jpg", b"photo")
    collect(client, pdf)
    body = {
        "a": {"document_id": str(pdf), "page": 1},
        "b": {"document_id": str(photo), "page": 1},
        "decision": "different",
    }
    assert client.post("/api/v1/ocr-samples/pairs", json=body).status_code == 200
    assert client.get("/api/v1/ocr-samples/pairs").json() == []


def test_manual_pairing_finds_units_by_filename(client, make):
    make("invoice-0412.pdf", b"x", pages=(1, 3))
    found = client.get("/api/v1/ocr-samples/units", params={"q": "0412"}).json()
    assert [(u["filename"], u["page"]) for u in found] == [
        ("invoice-0412.pdf", 1),
        ("invoice-0412.pdf", 3),
    ]


# ---- Merging, splitting and quarantine ----


def pair(client, a, b, decision="same"):
    body = {
        "a": {"document_id": str(a), "page": 1},
        "b": {"document_id": str(b), "page": 1},
        "decision": decision,
    }
    return client.post("/api/v1/ocr-samples/pairs", json=body).json()


def two_groups(client, factory, make, split_a="train", split_b="val"):
    turn_on(client)
    a, b = make("a.pdf", b"pdf"), make("a.jpg", b"photo")
    collect(client, a)
    collect(client, b)
    assert set_split(client, group_of(factory, a), split_a).status_code == 200
    assert set_split(client, group_of(factory, b), split_b).status_code == 200
    return a, b


def test_merge_before_any_export_follows_the_kept_group(client, factory, make):
    a, b = two_groups(client, factory, make)
    result = pair(client, a, b)
    assert (result["split"], result["quarantined"]) == ("train", False)


def test_duplicate_found_after_export_is_quarantined_and_recorded(client, factory, make):
    a, b = two_groups(client, factory, make)
    export(client)  # One copy in training, one in validation.
    result = pair(client, a, b)
    assert result["quarantined"] and result["affected_exports"] == 1
    with session_scope(factory) as session:
        [record] = session.query(OcrSampleExport).all()
        assert len(record.affected) == 2  # Both groups are in this export; one record each.
        for problem in record.affected:
            assert (
                problem["group"].startswith("g_")
                and "同时出现在训练集和验证集中" in problem["reason"]
            )
    excluded = {e["reason"]: e["count"] for e in preview(client)["excluded"]}
    assert excluded == {"所在分组已被隔离（合并或拆分时发现已导出）": 2}
    [shown] = client.get("/api/v1/ocr-samples/exports").json()
    assert shown["affected"] and shown["counts"]["train"]["groups"] == 1


def test_detach_before_export_gets_a_new_group(client, factory, make):
    a, b = two_groups(client, factory, make, "train", "train")
    pair(client, a, b)
    member = next(
        m
        for g in client.get("/api/v1/ocr-samples/groups").json()["items"]
        for m in g["members"]
        if m["document_id"] == str(b)
    )
    result = client.post(f"/api/v1/ocr-samples/groups/members/{member['id']}/detach").json()
    assert not result["quarantined"]
    assert group_of(factory, a) != group_of(factory, b)


def test_detach_after_export_quarantines_both(client, factory, make):
    a, b = two_groups(client, factory, make, "train", "train")
    pair(client, a, b)
    export(client)
    member = next(
        m
        for g in client.get("/api/v1/ocr-samples/groups").json()["items"]
        for m in g["members"]
        if m["document_id"] == str(b)
    )
    result = client.post(f"/api/v1/ocr-samples/groups/members/{member['id']}/detach").json()
    assert result["quarantined"] and result["affected_exports"] == 1
    splits = {g["split"] for g in client.get("/api/v1/ocr-samples/groups").json()["items"]}
    assert splits == {"quarantined"}


def test_a_group_exported_for_training_cannot_become_validation(client, factory, make):
    turn_on(client)
    a = make("a.png", b"a")
    collect(client, a)
    group = group_of(factory, a)
    set_split(client, group, "train")
    export(client)
    assert set_split(client, group, "val").status_code == 422
    assert set_split(client, group, "test_locked").status_code == 422


# ---- Export ----


def test_repeated_export_has_the_same_members(client, factory, make):
    turn_on(client)
    for n in range(6):
        doc = make(f"d{n}.png", f"d{n}".encode(), merchant=f"SHOP {n}")
        collect(client, doc)
        collect(client, doc, field="total", line=TOTAL_LINE, text="9.00")
    first, second = export(client), export(client)
    for name in ("rec_gt.txt", "manifest.jsonl", *(n for n in first.namelist() if "rec_gt_" in n)):
        assert first.read(name) == second.read(name)


def test_train_and_validation_never_share_a_group(client, factory, make):
    turn_on(client)
    for n in range(20):
        doc = make(f"d{n}.png", f"d{n}".encode(), merchant=f"SHOP {n}")
        collect(client, doc)
        collect(client, doc, field="total", line=TOTAL_LINE, text="9.00")
    archive = export(client)
    import json

    rows = [json.loads(r) for r in archive.read("manifest.jsonl").decode().splitlines()]
    train = {r["document_group"] for r in rows if r["split"] == "train"}
    val = {r["document_group"] for r in rows if r["split"] == "val"}
    assert train and not train & val
    shown = preview(client)
    assert shown["splits"]["train"]["groups"] + shown["splits"]["val"]["groups"] == 20
    assert shown["splits"]["val"]["rows"] == 2 * shown["splits"]["val"]["groups"]
    # Only a few validation groups: only the workflow can be checked.
    assert shown["evidence"]["level"] in ("process", "none")
    readme = archive.read("README.md").decode()
    assert "训练集：" in readme
    assert "仅可验证流程" in readme or "无验证集" in readme


def test_locked_test_set_is_exported_separately_and_not_a_lesson_source(client, factory, make):
    from jacara.pipeline.learn import from_database

    turn_on(client)
    a, b = make("a.png", b"a"), make("b.png", b"b", merchant="SHOP B")
    collect(client, a)
    collect(client, b)
    assert set_split(client, group_of(factory, a), "test_locked").status_code == 200
    excluded = {e["reason"]: e["count"] for e in preview(client)["excluded"]}
    assert excluded == {"锁定测试集（单独导出）": 1}
    test = export(client, kind="test")
    assert "rec_gt_test.txt" in test.namelist() and "rec_gt.txt" not in test.namelist()
    assert test.read("rec_gt_test.txt").decode().count("\n") == 1
    # Corrections after review completion: the copy in the locked test set is not used as an
    # example.
    with session_scope(factory) as session:
        for document in session.query(Document):
            document.status = "reviewed"
            document.reviewed_at = datetime(2026, 11, 13)
        team = session.get_one(Document, a).team_id
        lessons = from_database(session, team, "receipt")
        assert len(lessons) == 1  # Only b's.


def test_label_revision_goes_into_the_manifest(client, factory, make):
    import json

    turn_on(client)
    doc = make("a.png", b"a")
    collect(client, doc)
    sample = client.get("/api/v1/ocr-samples").json()["items"][0]
    client.patch(f"/api/v1/ocr-samples/{sample['id']}", json={"transcription": "1.0O"})
    client.patch(f"/api/v1/ocr-samples/{sample['id']}", json={"transcription": "1.00"})
    [row] = [json.loads(r) for r in export(client).read("manifest.jsonl").decode().splitlines()]
    assert row["sample_revision"] == 3


def test_older_samples_are_grouped_when_needed(client, factory, make):
    turn_on(client)
    doc = make("two.pdf", b"two", pages=(1, 2))
    collect(client, doc, page=2)
    # Collected before grouping existed: no group, first page defaults to 1.
    with session_scope(factory) as session:
        session.query(DocGroupMember).delete()
        for sample in session.query(OcrSample):
            sample.unit_page = 1
    # Backfilled on use (this image has one page, so cropping page 2 fails, without affecting
    # grouping).
    preview(client)
    with session_scope(factory) as session:
        assert session.query(OcrSample).one().unit_page == 2  # Backfilled from the source document.
        assert groups.member_of(session, (doc, 2)) is not None
