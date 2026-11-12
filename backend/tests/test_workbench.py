"""工作台与筛选、上一份 / 下一份、处理进度、导出记录、系统信息（ADR 0029）。"""

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from openpyxl import load_workbook

from jacara.core.db import session_scope
from jacara.models import Document, Export
from jacara.modules.documents.service import day_start
from jacara.pipeline.process import process_document
from jacara.pipeline.run import Pipeline
from jacara.pipeline.store import save_document
from jacara.plugins.extractors import LlmExtractor

from .test_process import FakeBackend, FakeOcr, uploaded  # noqa: F401  uploaded 是 fixture


@pytest.fixture
def docs(factory, report, tmp_path) -> dict[str, uuid.UUID]:
    """五份文档，上传时间一份比一份早一分钟：a 最新。

    a、b、d 待审核（done），c 已审核，e 失败。report 里有 3 个字段等人确认。
    """
    now = datetime.now(UTC)
    ids: dict[str, uuid.UUID] = {}
    for n, (name, status) in enumerate(
        [("a", "done"), ("b", "done"), ("c", "reviewed"), ("d", "done"), ("e", "failed")]
    ):
        path = tmp_path / f"{name}-costco.jpg"
        path.write_bytes(name.encode())
        with session_scope(factory) as session:
            document = save_document(session, path, [report])
            document.status = status
            document.created_at = now - timedelta(minutes=n)
            document.processed_at = now
            if status == "reviewed":
                document.reviewed_at = now
            ids[name] = document.id
    return ids


def names(client, query: str = "") -> list[str]:
    items = client.get(f"/api/v1/documents?{query}").json()["items"]
    return [d["filename"][0] for d in items]


# ---- 列表筛选 ----


def test_filters(client, docs):
    assert names(client) == ["a", "b", "c", "d", "e"]
    assert names(client, "status=done") == ["a", "b", "d"]
    assert names(client, "status=failed&status=reviewed") == ["c", "e"]
    assert names(client, "q=B-COST") == ["b"]  # 文件名，不分大小写
    assert names(client, "q=%25") == []  # % 是普通字符，不是"任意"
    assert names(client, "template=receipt") == ["a", "b", "c", "d", "e"]
    assert names(client, "template=invoice") == []
    today = datetime.now().astimezone().date()
    assert len(names(client, f"date_from={today - timedelta(days=1)}&date_to={today}")) == 5
    assert names(client, f"date_from={today + timedelta(days=1)}") == []
    assert names(client, "pending=true") == ["a", "b", "c", "d", "e"]  # 都还有 3 个字段没人看
    page = client.get("/api/v1/documents?status=done&limit=2").json()
    assert page["total"] == 3 and len(page["items"]) == 2


def test_pending_filter_skips_documents_with_everything_confirmed(client, docs):
    detail = client.get(f"/api/v1/documents/{docs['b']}").json()
    ids = [v["id"] for e in detail["extractions"] for v in e["values"] if v["pending_review"]]
    assert client.post("/api/v1/values/confirm", json={"ids": ids}).status_code == 200
    assert "b" not in names(client, "pending=true")


def test_day_start_is_local_midnight():
    start = day_start(datetime(2026, 11, 12).date())
    assert start.tzinfo is not None
    assert start.astimezone().hour == 0 and start.astimezone().day == 12


# ---- 上一份 / 下一份 ----


def neighbors(client, docs, name):
    data = client.get(f"/api/v1/documents/{docs[name]}/neighbors").json()
    back = {v: k for k, v in docs.items()}
    return (
        back.get(uuid.UUID(data["previous"])) if data["previous"] else None,
        back.get(uuid.UUID(data["next"])) if data["next"] else None,
        data["remaining"],
    )


def test_previous_and_next_only_count_documents_to_review(client, docs):
    assert neighbors(client, docs, "a") == (None, "b", 2)
    assert neighbors(client, docs, "b") == ("a", "d", 2)  # 跳过已审核的 c
    assert neighbors(client, docs, "d") == ("b", None, 2)
    # 自己已经审核完了：前后照样按列表顺序找，3 份都还待审核
    assert neighbors(client, docs, "c") == ("b", "d", 3)


# ---- 工作台 ----


def test_dashboard(client, docs):
    data = client.get("/api/v1/dashboard").json()
    counts = data["counts"]
    assert counts["to_review"] == 3 and counts["reviewed"] == 1 and counts["failed"] == 1
    assert counts["total"] == 5
    assert counts["pending_fields"] == 9  # 3 份 × 3 个
    assert counts["processed_today"] == 4  # 失败的不算
    assert counts["reviewed_today"] == 1
    assert [d["filename"][0] for d in data["failed"]] == ["e"]
    # 等人确认的一样多：先传的在前
    assert [d["filename"][0] for d in data["to_review"]] == ["d", "b", "a"]
    assert all(d["review_count"] == 3 for d in data["to_review"])
    assert data["queue"] == []


# ---- 处理进度 ----


def test_progress_is_reported_while_processing(factory, storage, uploaded, receipt_page):  # noqa: F811
    seen: list[tuple[str | None, int | None]] = []

    class Watching(FakeOcr):
        def recognize(self, image):
            with session_scope(factory) as session:
                d = session.get_one(Document, uploaded)
                seen.append((d.stage, d.progress))  # 识别文字时，数据库里已经写了进度
            return super().recognize(image)

    pipeline = Pipeline(ocr=Watching(receipt_page), extractor=LlmExtractor(FakeBackend()))
    assert process_document(uploaded, storage=storage, pipeline=pipeline, factory=factory) == "done"
    assert seen == [("识别文字", 5)]
    with session_scope(factory) as session:
        d = session.get_one(Document, uploaded)
        assert (d.stage, d.progress) == (None, None)  # 处理完清掉
        assert d.processed_at is not None


def test_pipeline_reports_each_page(receipt_page, tmp_path):
    steps: list[tuple[str, int]] = []
    pipeline = Pipeline(ocr=FakeOcr(receipt_page), extractor=LlmExtractor(FakeBackend()))
    path = tmp_path / "r.jpg"
    path.write_bytes(b"x")
    pipeline.run(path, progress=lambda stage, percent: steps.append((stage, percent)))
    assert steps == [("识别文字", 5), ("分析第 1 页的版面", 15), ("抽取第 1 页", 20)]


# ---- 导出记录 ----


def test_exports_are_kept_and_can_be_downloaded_again(client, docs, storage, factory):
    ids = f"ids={docs['a']}&ids={docs['b']}"
    first = client.get(f"/api/v1/documents/export.xlsx?{ids}")
    assert first.status_code == 200
    client.get(f"/api/v1/documents/export.zip?{ids}")

    page = client.get("/api/v1/exports").json()
    assert page["total"] == 2
    # 测试用的数据库时间只精确到秒，两次导出可能同一秒：按格式找
    older = next(x for x in page["items"] if x["format"] == "xlsx")
    assert {x["format"] for x in page["items"]} == {"xlsx", "zip"}
    assert older["document_count"] == 2
    assert [d["filename"] for d in older["documents"]] == ["a-costco.jpg", "b-costco.jpg"]
    assert older["user_name"] == "本机用户" and older["available"]

    again = client.get(f"/api/v1/exports/{older['id']}/file")
    assert again.status_code == 200
    assert again.content == first.content  # 当时导出的文件，一个字节都不差
    assert "汇总" in load_workbook(io.BytesIO(again.content)).sheetnames

    # 审计日志：导出和重新下载都有记录
    actions = [x["action"] for x in client.get("/api/v1/audit-logs").json()["items"]]
    assert actions.count("document:export") == 2 and "export:download" in actions


def test_export_still_works_when_storage_fails(client, docs, storage, monkeypatch):
    from jacara.core.storage import StorageError

    def broken(*_args):
        raise StorageError("down")

    monkeypatch.setattr(storage, "put", broken)
    assert client.get(f"/api/v1/documents/{docs['a']}/export.xlsx").status_code == 200
    [item] = client.get("/api/v1/exports").json()["items"]
    assert not item["available"]
    response = client.get(f"/api/v1/exports/{item['id']}/file")
    assert response.status_code == 404
    assert "没能保存" in response.json()["detail"]


def test_unknown_export(client):
    assert client.get(f"/api/v1/exports/{uuid.uuid4()}/file").status_code == 404


def test_export_rows_belong_to_the_team(factory, client, docs):
    client.get(f"/api/v1/documents/{docs['a']}/export.xlsx")
    with session_scope(factory) as session:
        [export] = session.query(Export).all()
        document = session.get_one(Document, docs["a"])
        assert (export.team_id, export.user_id) == (document.team_id, document.user_id)


# ---- 系统信息 ----


def test_system_info(client, docs, monkeypatch):
    from jacara.core.health import Check
    from jacara.modules.system import service

    monkeypatch.setattr(service, "run_checks", lambda _s: [Check("PostgreSQL", True, "版本 18")])
    monkeypatch.setattr(
        service,
        "model_props",
        lambda _server: {"model_path": "/x/models/Qwen.gguf", "n_ctx": 32768},
    )
    data = client.get("/api/v1/system").json()
    assert data["mode"] == "personal"
    assert data["model"]["loaded"] == "Qwen.gguf" and data["model"]["context"] == 32768
    assert data["ocr"]["engine"] == "rapidocr"
    assert "receipt" in [t["name"] for t in data["templates"]]
    assert data["data"]["documents"] == 5 and data["data"]["fields"] == 25
    assert data["services"] == [{"name": "PostgreSQL", "ok": True, "detail": "版本 18", "hint": ""}]


def test_model_props_when_the_model_is_not_running():
    from jacara.modules.system.service import model_props

    assert model_props("http://127.0.0.1:9") == {}
