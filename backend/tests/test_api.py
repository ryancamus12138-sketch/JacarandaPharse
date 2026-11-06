"""文档接口：用 FastAPI 的 TestClient 发请求，数据库用内存 SQLite，对象存储用内存里的假存储。

dependency_overrides 把真正的依赖（PostgreSQL、SeaweedFS）换成假的，
router 和 service 的代码一行不改。
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from jacara.app import create_app
from jacara.core.db import Base, session_scope
from jacara.core.deps import get_session, get_storage
from jacara.pipeline.store import save_document


class MemoryStorage:
    """假的对象存储：文件存在一个字典里。满足 ObjectStorage 接口。"""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        return self.files[key][0]


@pytest.fixture
def factory() -> sessionmaker[Session]:
    # StaticPool：所有请求共用同一个内存数据库连接，否则每个连接都是一个新的空数据库
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def client(factory, storage) -> Iterator[TestClient]:
    app = create_app()

    def test_session() -> Iterator[Session]:
        with session_scope(factory) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_storage] = lambda: storage
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
