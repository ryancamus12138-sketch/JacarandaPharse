"""Dashboard and filters, previous / next, processing progress, export history, system
information."""

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from openpyxl import load_workbook

from jacara.core.db import session_scope
from jacara.core.i18n import render
from jacara.models import Document, Export
from jacara.modules.documents.service import day_start
from jacara.pipeline.process import process_document
from jacara.pipeline.run import Pipeline
from jacara.pipeline.store import save_document
from jacara.plugins.extractors import LlmExtractor

from .test_process import FakeBackend, FakeOcr, uploaded  # noqa: F401  uploaded is a fixture.


@pytest.fixture
def docs(factory, report, tmp_path) -> dict[str, uuid.UUID]:
    """Five documents, each uploaded a minute before the previous one: a is newest.

        a, b and d await review (done), c is reviewed, e failed. report has 3 fields awaiting
    confirmation."""
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


# ---- List filters ----


def test_filters(client, docs):
    assert names(client) == ["a", "b", "c", "d", "e"]
    assert names(client, "status=done") == ["a", "b", "d"]
    assert names(client, "status=failed&status=reviewed") == ["c", "e"]
    assert names(client, "q=B-COST") == ["b"]  # File name, case-insensitive.
    assert names(client, "q=%25") == []  # % is a literal character, not a wildcard.
    assert names(client, "template=receipt") == ["a", "b", "c", "d", "e"]
    assert names(client, "template=invoice") == []
    today = datetime.now().astimezone().date()
    assert len(names(client, f"date_from={today - timedelta(days=1)}&date_to={today}")) == 5
    assert names(client, f"date_from={today + timedelta(days=1)}") == []
    # All still have 3 unreviewed fields.
    assert names(client, "pending=true") == ["a", "b", "c", "d", "e"]
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


# ---- Previous / next ----


def neighbors(client, docs, name, query=""):
    data = client.get(f"/api/v1/documents/{docs[name]}/neighbors?{query}").json()
    back = {str(v): k for k, v in docs.items()}
    return (
        back.get(data["previous"]),
        back.get(data["next"]),
        data["position"],
        data["total"],
        back.get(data["next_to_review"]),
        data["remaining"],
    )


def test_previous_and_next_page_through_the_whole_list(client, docs):
    # Any status, in list order: a b c d e.
    assert neighbors(client, docs, "a")[:4] == (None, "b", 1, 5)
    assert neighbors(client, docs, "c")[:4] == ("b", "d", 3, 5)  # Reviewed documents included.
    assert neighbors(client, docs, "e")[:4] == ("d", None, 5, 5)  # Failed documents included.


def test_previous_and_next_follow_the_list_filters(client, docs):
    assert neighbors(client, docs, "b", "status=done")[:4] == ("a", "d", 2, 3)
    assert neighbors(client, docs, "d", "status=done&status=failed")[:4] == ("b", "e", 3, 4)
    # c is not in this list (for example reviewed meanwhile): its place in the order still works.
    assert neighbors(client, docs, "c", "status=done")[:4] == ("b", "d", None, 3)
    assert neighbors(client, docs, "a", "q=zzz")[:4] == (None, None, None, 0)


def test_next_to_review_only_counts_documents_to_review(client, docs):
    assert neighbors(client, docs, "a")[4:] == ("b", 2)
    assert neighbors(client, docs, "b")[4:] == ("d", 2)  # Skips the reviewed c.
    assert neighbors(client, docs, "d")[4:] == ("b", 2)  # Last one: falls back to the previous.
    # This one is already reviewed: still follows list order, and 3 documents await review.
    assert neighbors(client, docs, "c")[4:] == ("d", 3)


# ---- Dashboard ----


def test_dashboard(client, docs):
    data = client.get("/api/v1/dashboard").json()
    counts = data["counts"]
    assert counts["to_review"] == 3 and counts["reviewed"] == 1 and counts["failed"] == 1
    assert counts["total"] == 5
    assert counts["pending_fields"] == 9  # 3 documents × 3 fields.
    assert counts["processed_today"] == 4  # Failures are not counted.
    assert counts["reviewed_today"] == 1
    assert [d["filename"][0] for d in data["failed"]] == ["e"]
    # Equal numbers awaiting confirmation: earliest upload first.
    assert [d["filename"][0] for d in data["to_review"]] == ["d", "b", "a"]
    assert all(d["review_count"] == 3 for d in data["to_review"])
    assert data["queue"] == []


# ---- Processing progress ----


def test_progress_is_reported_while_processing(factory, storage, uploaded, receipt_page):  # noqa: F811
    seen: list[tuple[str | None, int | None]] = []

    class Watching(FakeOcr):
        def recognize(self, image):
            with session_scope(factory) as session:
                d = session.get_one(Document, uploaded)
                # Progress is already in the database during OCR.
                seen.append((render(d.stage), d.progress))
            return super().recognize(image)

    pipeline = Pipeline(ocr=Watching(receipt_page), extractor=LlmExtractor(FakeBackend()))
    assert process_document(uploaded, storage=storage, pipeline=pipeline, factory=factory) == "done"
    assert seen == [("文字识别", 5)]
    with session_scope(factory) as session:
        d = session.get_one(Document, uploaded)
        assert (d.stage, d.progress) == (None, None)  # Cleared when finished.
        assert d.processed_at is not None


def test_pipeline_reports_each_page(receipt_page, tmp_path):
    steps: list[tuple[str, int]] = []
    pipeline = Pipeline(ocr=FakeOcr(receipt_page), extractor=LlmExtractor(FakeBackend()))
    path = tmp_path / "r.jpg"
    path.write_bytes(b"x")
    pipeline.run(path, progress=lambda stage, percent: steps.append((render(stage), percent)))
    assert steps == [("文字识别", 5), ("分析第 1 页的版面", 15), ("抽取第 1 页", 20)]


# ---- Export history ----


def test_exports_are_kept_and_can_be_downloaded_again(client, docs, storage, factory):
    ids = f"ids={docs['a']}&ids={docs['b']}"
    first = client.get(f"/api/v1/documents/export.xlsx?{ids}")
    assert first.status_code == 200
    client.get(f"/api/v1/documents/export.zip?{ids}")

    page = client.get("/api/v1/exports").json()
    assert page["total"] == 2
    # The test database has second precision, so two exports may share a timestamp: look up by
    # format.
    older = next(x for x in page["items"] if x["format"] == "xlsx")
    assert {x["format"] for x in page["items"]} == {"xlsx", "zip"}
    assert older["document_count"] == 2
    assert [d["filename"] for d in older["documents"]] == ["a-costco.jpg", "b-costco.jpg"]
    assert older["user_name"] == "我" and older["available"]

    again = client.get(f"/api/v1/exports/{older['id']}/file")
    assert again.status_code == 200
    assert again.content == first.content  # The file as exported at the time, byte for byte.
    assert "汇总" in load_workbook(io.BytesIO(again.content)).sheetnames

    # Audit log: both export and re-download are recorded.
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
    assert "未能保存" in response.json()["detail"]


def test_unknown_export(client):
    assert client.get(f"/api/v1/exports/{uuid.uuid4()}/file").status_code == 404


def test_export_rows_belong_to_the_team(factory, client, docs):
    client.get(f"/api/v1/documents/{docs['a']}/export.xlsx")
    with session_scope(factory) as session:
        [export] = session.query(Export).all()
        document = session.get_one(Document, docs["a"])
        assert (export.team_id, export.user_id) == (document.team_id, document.user_id)


# ---- System information ----


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
