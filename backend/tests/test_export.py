"""Export API: download Excel and inspect it; reading DocumentReport back from the database
(load_reports)."""

import io
import uuid
import zipfile
from urllib.parse import unquote

import pytest
from openpyxl import load_workbook

from jacara.core.db import session_scope
from jacara.models import Document
from jacara.pipeline.load import load_reports
from jacara.pipeline.store import save_document

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def save(factory, report, tmp_path):
    """Store a processed document and return its id. The file name is optional."""

    def _save(filename: str = "receipt.jpg") -> uuid.UUID:
        path = tmp_path / filename
        path.write_bytes(b"x")
        with session_scope(factory) as session:
            return save_document(session, path, [report]).id

    return _save


def summary(response) -> list[dict]:
    """Read the summary sheet of a downloaded workbook as one dictionary per row."""
    sheet = load_workbook(io.BytesIO(response.content))["汇总"]
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


def download_name(response) -> str:
    """Decoded file name from Content-Disposition."""
    disposition = response.headers["content-disposition"]
    return unquote(disposition.split("filename*=UTF-8''")[1])


def test_export_one_document(client, save):
    document_id = save()
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"] == XLSX
    assert response.headers["content-disposition"].startswith("attachment")
    assert download_name(response) == "receipt.xlsx"
    [row] = summary(response)
    assert row["总额"] == 9.0  # Column headers use the template display names.
    assert row["需要确认"] == 3


def test_export_uses_reviewed_value(client, save):
    document_id = save()
    detail = client.get(f"/api/v1/documents/{document_id}").json()
    change = next(v for v in detail["extractions"][0]["values"] if v["name"] == "change")
    client.post(
        f"/api/v1/values/{change['id']}/revisions",
        json={"action": "edit", "value": "1.00", "reason": "OCR 读错"},
    )

    [row] = summary(client.get(f"/api/v1/documents/{document_id}/export.xlsx"))
    assert row["change"] == 1.0  # The post-review value, not the model's 100.
    assert row["需要确认"] == 2


def test_export_many_documents_in_order(client, save):
    first, second = save("a.jpg"), save("b.jpg")
    response = client.get(
        "/api/v1/documents/export.xlsx", params={"ids": [str(second), str(first), str(second)]}
    )

    assert response.status_code == 200
    # In selection order, without duplicates.
    assert [r["文件"] for r in summary(response)] == ["b.jpg", "a.jpg"]
    assert download_name(response).endswith("2份.xlsx")


def test_export_zip_one_excel_per_document(client, save):
    ids = [save("receipt.jpg"), save("发票.pdf"), save("receipt.jpg")]
    response = client.get("/api/v1/documents/export.zip", params={"ids": [str(i) for i in ids]})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert download_name(response).endswith("3份.zip")
    assert 'filename="export.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        # In selection order; duplicate names get (2).
        assert archive.namelist() == ["receipt.xlsx", "发票.xlsx", "receipt (2).xlsx"]
        for name in archive.namelist():
            sheet = load_workbook(io.BytesIO(archive.read(name)))["汇总"]
            assert sheet.max_row == 2  # Header + this document.


def test_export_zip_checks_like_excel(client, factory, save):
    document_id = save()
    with session_scope(factory) as session:
        session.get_one(Document, document_id).status = "processing"
    response = client.get("/api/v1/documents/export.zip", params={"ids": [str(document_id)]})
    assert response.status_code == 409


def test_chinese_file_name(client, save):
    document_id = save("收据.jpg")
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    assert download_name(response) == "收据.xlsx"


def test_unfinished_document_is_409(client, factory, save):
    document_id = save()
    with session_scope(factory) as session:
        session.get_one(Document, document_id).status = "processing"
    response = client.get(f"/api/v1/documents/{document_id}/export.xlsx")
    assert response.status_code == 409
    assert "receipt.jpg" in response.json()["detail"]


def test_unknown_document_is_404(client):
    response = client.get(f"/api/v1/documents/{uuid.uuid4()}/export.xlsx")
    assert response.status_code == 404


def test_export_many_needs_ids(client):
    assert client.get("/api/v1/documents/export.xlsx").status_code == 422


def test_load_reports_round_trip(factory, report, save):
    """Everything exporters need survives a database round trip."""
    document_id = save()
    with session_scope(factory) as session:
        [loaded] = load_reports(session.get_one(Document, document_id))

    assert [f.value for f in loaded.extraction.fields] == [
        f.value for f in report.extraction.fields
    ]
    assert loaded.checks.fields_with_issues() == report.checks.fields_with_issues()
    original = {a.field.name: a for a in report.aligned}
    for field in loaded.aligned:
        before = original[field.field.name]
        assert field.status == before.status
        if before.evidence is not None:
            assert field.evidence is not None
            assert field.evidence.bbox == pytest.approx(before.evidence.bbox, abs=0.1)
    assert loaded.reviews == {}  # Not yet reviewed.
