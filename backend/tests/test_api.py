"""文档接口：用 FastAPI 的 TestClient 发请求。

dependency_overrides 把真正的依赖（PostgreSQL、SeaweedFS、Valkey）换成假的（见 conftest.py），
router 和 service 的代码一行不改。测试不会往真的队列里放任务。
"""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from jacara.app import create_app
from jacara.core.db import session_scope
from jacara.core.deps import get_queue, get_session, get_storage
from jacara.models import Document
from jacara.pipeline.store import save_document


@pytest.fixture
def client(factory, storage, queue) -> Iterator[TestClient]:
    app = create_app()

    def test_session() -> Iterator[Session]:
        with session_scope(factory) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_queue] = lambda: queue
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, name: str, data: bytes, content_type: str):
    return client.post("/api/v1/documents", files={"file": (name, data, content_type)})


def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_upload_saves_file_and_record(client, storage):
    response = upload(client, "receipt.jpg", b"fake image bytes", "image/jpeg")

    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "receipt.jpg"
    assert body["status"] == "uploaded"
    assert body["page_count"] == 1
    assert "storage_key" not in body  # 内部信息不给前端
    assert storage.files[f"documents/{body['id']}/receipt.jpg"][0] == b"fake image bytes"


def test_upload_rejects_unsupported_type(client):
    response = upload(client, "notes.txt", b"hello", "text/plain")
    assert response.status_code == 415
    assert "不支持" in response.json()["detail"]


def test_upload_guesses_type_from_file_name(client):
    response = upload(client, "scan.png", b"x", "application/octet-stream")
    assert response.status_code == 201
    assert response.json()["content_type"] == "image/png"


def test_upload_rejects_empty_file(client):
    assert upload(client, "empty.jpg", b"", "image/jpeg").status_code == 422


def test_upload_keeps_only_the_file_name(client, storage):
    body = upload(client, "../../etc/passwd.png", b"x", "image/png").json()
    assert body["filename"] == "passwd.png"  # 不能用文件名跳到别的目录
    assert all(".." not in key for key in storage.files)


def test_storage_failure_is_503_and_nothing_saved(client, storage, monkeypatch):
    from jacara.core.storage import StorageError

    def broken(key: str, data: bytes, content_type: str) -> None:
        raise StorageError("连不上")

    monkeypatch.setattr(storage, "put", broken)
    response = upload(client, "receipt.jpg", b"x", "image/jpeg")
    assert response.status_code == 503
    assert client.get("/api/v1/documents").json()["total"] == 0  # 文件没存成，就不写数据库


def test_list_newest_first_with_total(client):
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        upload(client, name, b"x", "image/jpeg")

    page = client.get("/api/v1/documents", params={"limit": 2}).json()
    assert page["total"] == 3
    assert len(page["items"]) == 2


def test_limit_is_validated(client):
    assert client.get("/api/v1/documents", params={"limit": 1000}).status_code == 422


def test_detail_includes_values_and_review_count(client, factory, report, tmp_path):
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        document_id = save_document(session, path, [report]).id

    detail = client.get(f"/api/v1/documents/{document_id}").json()
    assert detail["status"] == "done"
    [extraction] = detail["extractions"]
    names = [v["name"] for v in extraction["values"]]
    assert names == ["merchant", "total", "cash", "change", "member"]
    assert detail["review_count"] == 3  # cash、total、change：找零规则不通过

    listed = client.get("/api/v1/documents").json()["items"][0]
    assert listed["review_count"] == 3


def test_unknown_document_is_404(client):
    response = client.get("/api/v1/documents/00000000-0000-0000-0000-000000000099")
    assert response.status_code == 404


def test_download_returns_original_file(client):
    body = upload(client, "scan.png", b"\x89PNG fake", "image/png").json()
    response = client.get(f"/api/v1/documents/{body['id']}/file")
    assert response.status_code == 200
    assert response.content == b"\x89PNG fake"
    assert response.headers["content-type"] == "image/png"


def test_docs_page_works_offline(client):
    page = client.get("/docs").text
    assert "cdn.jsdelivr.net" not in page  # 不从网上加载界面文件
    assert client.get("/static-offline-docs/swagger-ui-bundle.js").status_code == 200


def test_upload_puts_document_in_queue(client, queue):
    body = upload(client, "receipt.jpg", b"x", "image/jpeg").json()
    assert queue.sent == [uuid.UUID(body["id"])]


def test_upload_still_works_when_queue_is_down(client, queue):
    queue.down = True
    response = upload(client, "receipt.jpg", b"x", "image/jpeg")
    assert response.status_code == 201  # 文件和记录都保存了，之后可以重新处理
    assert response.json()["status"] == "uploaded"


def _set_status(factory, document_id: str, status: str, error: str | None = None) -> None:
    with session_scope(factory) as session:
        document = session.get_one(Document, uuid.UUID(document_id))
        document.status = status
        document.error = error


def test_failed_document_can_be_processed_again(client, factory, queue):
    document_id = upload(client, "receipt.jpg", b"x", "image/jpeg").json()["id"]
    _set_status(factory, document_id, "failed", "大模型服务连不上")

    response = client.post(f"/api/v1/documents/{document_id}/process")
    assert response.status_code == 202
    assert response.json()["status"] == "uploaded"
    assert response.json()["error"] is None  # 旧的失败原因清掉
    assert queue.sent == [uuid.UUID(document_id)] * 2  # 上传一次，重新处理一次


def test_done_document_is_not_processed_again(client, factory):
    document_id = upload(client, "receipt.jpg", b"x", "image/jpeg").json()["id"]
    _set_status(factory, document_id, "done")
    assert client.post(f"/api/v1/documents/{document_id}/process").status_code == 409


def test_process_when_queue_is_down_is_503(client, queue):
    document_id = upload(client, "receipt.jpg", b"x", "image/jpeg").json()["id"]
    queue.down = True
    assert client.post(f"/api/v1/documents/{document_id}/process").status_code == 503
