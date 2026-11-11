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
