"""导出 API：下载 Excel，打开检查内容。以及从数据库读回 DocumentReport（load_reports）。"""

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
    """存一个处理完的文档，返回它的 id。可以指定文件名。"""

    def _save(filename: str = "receipt.jpg") -> uuid.UUID:
        path = tmp_path / filename
        path.write_bytes(b"x")
        with session_scope(factory) as session:
            return save_document(session, path, [report]).id

    return _save


def summary(response) -> list[dict]:
    """把下载的 Excel 的"汇总"表读成一行一个字典。"""
    sheet = load_workbook(io.BytesIO(response.content))["汇总"]
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


def download_name(response) -> str:
    """从 Content-Disposition 里取出（解码后的）文件名。"""
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
    assert row["总额"] == 9.0  # 列名用模板里的中文名
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
    assert row["change"] == 1.0  # 审核之后的值，不是模型的 100
    assert row["需要确认"] == 2


def test_export_many_documents_in_order(client, save):
    first, second = save("a.jpg"), save("b.jpg")
    response = client.get(
        "/api/v1/documents/export.xlsx", params={"ids": [str(second), str(first), str(second)]}
    )

    assert response.status_code == 200
    assert [r["文件"] for r in summary(response)] == ["b.jpg", "a.jpg"]  # 按选的顺序，去掉重复
    assert download_name(response).endswith("2份.xlsx")


def test_export_zip_one_excel_per_document(client, save):
    ids = [save("receipt.jpg"), save("发票.pdf"), save("receipt.jpg")]
    response = client.get("/api/v1/documents/export.zip", params={"ids": [str(i) for i in ids]})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert download_name(response).endswith("3份.zip")
    assert 'filename="export.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        # 按选的顺序；重名的加 (2)
        assert archive.namelist() == ["receipt.xlsx", "发票.xlsx", "receipt (2).xlsx"]
        for name in archive.namelist():
            sheet = load_workbook(io.BytesIO(archive.read(name)))["汇总"]
            assert sheet.max_row == 2  # 表头 + 这一张单据


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
    """存进数据库再读出来，导出器需要的东西都还在。"""
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
    assert loaded.reviews == {}  # 还没人审核
