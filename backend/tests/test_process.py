"""后台处理：用假的 OCR、假的模型、假的存储，测试状态变化和结果保存。不需要启动 Valkey 和 worker。

process_document 是 worker 任务里真正干活的函数；Celery 只负责"把它叫起来"。
"""

import json
import uuid

import pytest

from jacara.core.db import session_scope
from jacara.core.owner import PERSONAL_OWNER
from jacara.models import Document
from jacara.pipeline.process import process_document, reset_interrupted
from jacara.pipeline.run import Pipeline
from jacara.plugins.extractors import LlmExtractor
from jacara.plugins.model_backends import ChatResult, ModelBackendError

ANSWER = json.dumps(
    {
        "merchant": {"value": "BOOK TA _K (TAMAN DAYA) SDN BHD", "row": 1},
        "store_name": {"value": "BOOK TA _K (TAMAN DAYA) SDN BHD", "row": 1},
        "document_no": {"value": None, "row": None},
        "date": {"value": "2018-12-25", "row": 4},
        "total": {"value": 9.0, "row": 6},
    }
)


class FakeOcr:
    """假的 OCR：不管给什么图片，都返回 conftest 里那张收据的识别结果。"""

    name = "fake-ocr"

    def __init__(self, page) -> None:
        self.page = page
        self.seen_suffix = ""

    def recognize(self, image):
        self.seen_suffix = image.suffix
        return self.page


class FakeBackend:
    name = "fake"

    def __init__(self, answer: str = ANSWER, error: str | None = None) -> None:
        self.answer = answer
        self.error = error

    def chat(self, messages, *, json_schema=None):
        if self.error:
            raise ModelBackendError(self.error)
        return ChatResult(self.answer, "fake-model", self.name, 0, 0, 1)


@pytest.fixture
def uploaded(factory, storage) -> uuid.UUID:
    """一个刚通过 API 上传、还没处理的文档。"""
    document_id = uuid.uuid4()
    key = f"documents/{document_id}/receipt"
    storage.put(key, b"fake image", "image/jpeg")
    with session_scope(factory) as session:
        session.add(
            Document(
                id=document_id,
                filename="receipt",  # 故意不带后缀：要靠 content_type 判断是图片
                content_type="image/jpeg",
                sha256="0" * 64,
                size_bytes=10,
                page_count=1,
                storage_key=key,
                status="uploaded",
                user_id=PERSONAL_OWNER.user_id,
                team_id=PERSONAL_OWNER.team_id,
            )
        )
    return document_id


def run(factory, storage, document_id, ocr, backend=None):
    pipeline = Pipeline(ocr=ocr, extractor=LlmExtractor(backend or FakeBackend()))
    return process_document(document_id, storage=storage, pipeline=pipeline, factory=factory)


def test_success_saves_results_and_marks_done(factory, storage, uploaded, receipt_page):
    ocr = FakeOcr(receipt_page)
    assert run(factory, storage, uploaded, ocr) == "done"
    assert ocr.seen_suffix == ".jpg"  # 按 content_type 补上后缀，流水线才知道是图片

    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert document.status == "done"
        assert document.error is None
        [extraction] = document.extractions
        assert extraction.model == "fake-model"
        assert extraction.user_id == document.user_id  # 抽取结果和文档归属一致
        values = {v.name: v.value for v in extraction.values}
        assert values["total"] == "9.0"


def test_model_error_marks_failed_with_reason(factory, storage, uploaded, receipt_page):
    backend = FakeBackend(error="连不上模型服务 http://127.0.0.1:8080")
    assert run(factory, storage, uploaded, FakeOcr(receipt_page), backend) == "failed"

    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert document.status == "failed"
        assert document.error == "连不上模型服务 http://127.0.0.1:8080"
        assert document.extractions == []  # 失败时不留半份结果


def test_missing_file_marks_failed(factory, storage, uploaded, receipt_page):
    storage.files.clear()  # 对象存储里的文件不见了
    assert run(factory, storage, uploaded, FakeOcr(receipt_page)) == "failed"
    with session_scope(factory) as session:
        assert "原始文件" in (session.get_one(Document, uploaded).error or "")


def test_same_task_twice_only_processes_once(factory, storage, uploaded, receipt_page):
    ocr = FakeOcr(receipt_page)
    assert run(factory, storage, uploaded, ocr) == "done"
    assert run(factory, storage, uploaded, ocr) == "skipped"  # 状态已经是 done，不再处理
    with session_scope(factory) as session:
        assert len(session.get_one(Document, uploaded).extractions) == 1


def test_unknown_document_is_skipped(factory, storage, receipt_page):
    assert run(factory, storage, uuid.uuid4(), FakeOcr(receipt_page)) == "skipped"


def test_interrupted_documents_become_failed(factory, uploaded):
    with session_scope(factory) as session:
        session.get_one(Document, uploaded).status = "processing"

    assert reset_interrupted(factory) == 1
    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert document.status == "failed"
        assert document.error is not None


# ---- 流水线：OCR 工位和模型工位（ADR 0051） ----


class Sent:
    """假的"交给模型工位"：记下交了什么。down=True 模拟队列连不上。"""

    def __init__(self) -> None:
        self.items: list[tuple[uuid.UUID, str]] = []
        self.down = False

    def __call__(self, document_id: uuid.UUID, attempt: str) -> None:
        if self.down:
            from jacara.core.queue import QueueError

            raise QueueError("任务队列（Valkey）连不上")
        self.items.append((document_id, attempt))


def two_stations(factory, storage, document_id, ocr, backend=None):
    from jacara.pipeline.process import extract_document, read_document

    pipeline = Pipeline(ocr=ocr, extractor=LlmExtractor(backend or FakeBackend()))
    sent = Sent()
    read = read_document(
        document_id, storage=storage, pipeline=pipeline, send=sent, factory=factory
    )
    return (
        read,
        sent,
        lambda attempt: extract_document(
            document_id, attempt, storage=storage, pipeline=pipeline, factory=factory
        ),
    )


def test_two_stations_give_the_same_result(factory, storage, uploaded, receipt_page):
    ocr = FakeOcr(receipt_page)
    read, sent, extract = two_stations(factory, storage, uploaded, ocr)
    assert read == "read"
    [(document_id, attempt)] = sent.items
    assert document_id == uploaded
    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert document.status == "processing"  # 认好字了，等模型
        assert document.stage == "文字已认好，排队等模型"
    assert f"pipeline/{uploaded}/{attempt}.json" in storage.files

    ocr.page = None  # 模型工位不再认字：用 OCR 工位交过来的
    assert extract(attempt) == "done"
    assert not any(k.startswith("pipeline/") for k in storage.files)  # 交接文件做完就删
    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert document.status == "done"
        values = {v.name: v.value for v in document.extractions[0].values}
        assert values["total"] == "9.0"
        timings = document.timings or {}
        assert timings["split"] is True
        assert {"read_ms", "wait_ms", "extract_ms"} <= set(timings)


def test_one_process_records_timings_too(factory, storage, uploaded, receipt_page):
    assert run(factory, storage, uploaded, FakeOcr(receipt_page)) == "done"
    with session_scope(factory) as session:
        timings = session.get_one(Document, uploaded).timings or {}
        assert timings["split"] is False and timings["wait_ms"] == 0


def test_stale_extract_task_is_skipped(factory, storage, uploaded, receipt_page):
    """重新处理以后，上一次留在队列里的旧任务不能拿旧的字去抽。"""
    read, _, extract = two_stations(factory, storage, uploaded, FakeOcr(receipt_page))
    assert read == "read"
    assert extract("old-attempt") == "skipped"
    with session_scope(factory) as session:
        assert session.get_one(Document, uploaded).status == "processing"  # 真的那个还在等


def test_queue_down_after_reading_marks_failed(factory, storage, uploaded, receipt_page):
    from jacara.pipeline.process import read_document

    pipeline = Pipeline(ocr=FakeOcr(receipt_page), extractor=LlmExtractor(FakeBackend()))
    sent = Sent()
    sent.down = True
    outcome = read_document(
        uploaded, storage=storage, pipeline=pipeline, send=sent, factory=factory
    )
    assert outcome == "failed"
    assert not any(k.startswith("pipeline/") for k in storage.files)
    with session_scope(factory) as session:
        assert session.get_one(Document, uploaded).status == "failed"


def test_model_error_at_the_second_station(factory, storage, uploaded, receipt_page):
    backend = FakeBackend(error="连不上模型服务 http://127.0.0.1:8080")
    _, sent, extract = two_stations(factory, storage, uploaded, FakeOcr(receipt_page), backend)
    assert extract(sent.items[0][1]) == "failed"
    assert not any(k.startswith("pipeline/") for k in storage.files)
    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert (document.status, document.error) == (
            "failed",
            "连不上模型服务 http://127.0.0.1:8080",
        )


def test_handoff_keeps_everything(receipt_page):
    from dataclasses import replace

    from jacara.pipeline.handoff import dump_pages, load_pages
    from jacara.plugins.ocr.base import FormValue

    page = replace(
        receipt_page,
        models={"rec": "PP-OCRv5"},
        text_layer=receipt_page.lines[:2],
        forms=(FormValue("Amount", "9.00", (1.0, 2.0, 3.0, 4.0), hidden=True),),
    )
    assert load_pages(dump_pages([page, receipt_page])) == [page, receipt_page]


# ---- 每一轮的 OCR 快照、发给模型的文字（ADR 0053） ----


@pytest.mark.parametrize("split", [False, True])
def test_each_run_keeps_an_ocr_snapshot(factory, storage, uploaded, receipt_page, split):
    """一个进程做完、两个工位做完，都留下这一轮的 OCR 快照，读回来和认出来的一模一样。"""
    from jacara.models import Extraction
    from jacara.pipeline.snapshots import load_snapshot, snapshot_key

    if split:
        _, sent, extract = two_stations(factory, storage, uploaded, FakeOcr(receipt_page))
        assert extract(sent.items[0][1]) == "done"
    else:
        assert run(factory, storage, uploaded, FakeOcr(receipt_page)) == "done"
    key = snapshot_key(uploaded, 1)
    assert load_snapshot(storage, key) == [receipt_page]
    with session_scope(factory) as session:
        [extraction] = session.query(Extraction).all()
        assert extraction.ocr_snapshot == key


def test_what_was_sent_to_the_model_is_recorded(factory, storage, uploaded, receipt_page):
    from jacara.models import Extraction

    assert run(factory, storage, uploaded, FakeOcr(receipt_page)) == "done"
    with session_scope(factory) as session:
        [extraction] = session.query(Extraction).all()
        sent = extraction.model_input or {}
    assert sent["blocks"] == list(range(1, len(receipt_page.lines) + 1))
    assert sent["total_blocks"] == len(receipt_page.lines)
    assert sent["images"] == 0 and sent["lessons"] == 0
    assert receipt_page.lines[0].text in sent["text"]
    assert sent["chars"] == len(sent["text"]) and len(sent["sha256"]) == 64


def test_snapshot_failure_does_not_fail_processing(factory, storage, uploaded, receipt_page):
    """快照存不进去只记日志，处理结果照常保存。"""
    from jacara.core.storage import StorageError

    put = storage.put

    def refuse(key, data, content_type):
        if key.startswith("ocr-snapshots/"):
            raise StorageError("满了")
        put(key, data, content_type)

    storage.put = refuse
    assert run(factory, storage, uploaded, FakeOcr(receipt_page)) == "done"
