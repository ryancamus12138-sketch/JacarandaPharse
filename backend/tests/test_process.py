"""Background processing with fake OCR, model and storage: status transitions and stored results. No
Valkey or worker required.

process_document does the actual work of the worker task; Celery only invokes it."""

import json
import uuid

import pytest

from jacara.core.db import session_scope
from jacara.core.i18n import render
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
    """Fake OCR: returns the recognition result of the conftest receipt for any image."""

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
    """A document just uploaded through the API and not yet processed."""
    document_id = uuid.uuid4()
    key = f"documents/{document_id}/receipt"
    storage.put(key, b"fake image", "image/jpeg")
    with session_scope(factory) as session:
        session.add(
            Document(
                id=document_id,
                # Deliberately without extension: the image type must come from content_type.
                filename="receipt",
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
    # The extension derived from content_type tells the pipeline it is an image.
    assert ocr.seen_suffix == ".jpg"

    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert document.status == "done"
        assert document.error is None
        [extraction] = document.extractions
        assert extraction.model == "fake-model"
        assert extraction.user_id == document.user_id  # Extraction ownership matches the document.
        values = {v.name: v.value for v in extraction.values}
        assert values["total"] == "9.0"


def test_model_error_marks_failed_with_reason(factory, storage, uploaded, receipt_page):
    backend = FakeBackend(error="连不上模型服务 http://127.0.0.1:8080")
    assert run(factory, storage, uploaded, FakeOcr(receipt_page), backend) == "failed"

    with session_scope(factory) as session:
        document = session.get_one(Document, uploaded)
        assert document.status == "failed"
        assert document.error == "连不上模型服务 http://127.0.0.1:8080"
        assert document.extractions == []  # No partial results on failure.


def test_missing_file_marks_failed(factory, storage, uploaded, receipt_page):
    storage.files.clear()  # The file is missing from object storage.
    assert run(factory, storage, uploaded, FakeOcr(receipt_page)) == "failed"
    with session_scope(factory) as session:
        assert "原始文件" in (session.get_one(Document, uploaded).error or "")


def test_same_task_twice_only_processes_once(factory, storage, uploaded, receipt_page):
    ocr = FakeOcr(receipt_page)
    assert run(factory, storage, uploaded, ocr) == "done"
    # Status is already done; not processed again.
    assert run(factory, storage, uploaded, ocr) == "skipped"
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


# ---- Pipeline: OCR stage and model stage ----


class Sent:
    """Fake hand-off to the model stage: records what was handed off. down=True simulates an
    unreachable queue."""

    def __init__(self) -> None:
        self.items: list[tuple[uuid.UUID, str]] = []
        self.down = False

    def __call__(self, document_id: uuid.UUID, attempt: str) -> None:
        if self.down:
            from jacara.core.queue import QueueError

            raise QueueError("无法连接任务队列（Valkey）")
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
        assert document.status == "processing"  # OCR done; waiting for the model.
        assert render(document.stage) == "文字识别完成，排队等待模型"
    assert f"pipeline/{uploaded}/{attempt}.json" in storage.files

    ocr.page = None  # The model stage does not run OCR again: it uses the OCR stage output.
    assert extract(attempt) == "done"
    # The hand-off file is deleted when done.
    assert not any(k.startswith("pipeline/") for k in storage.files)
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
    """After reprocessing, a stale task left in the queue must not extract from the old text."""
    read, _, extract = two_stations(factory, storage, uploaded, FakeOcr(receipt_page))
    assert read == "read"
    assert extract("old-attempt") == "skipped"
    with session_scope(factory) as session:
        # The real one is still waiting.
        assert session.get_one(Document, uploaded).status == "processing"


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


# ---- OCR snapshots per run and text sent to the model ----


@pytest.mark.parametrize("split", [False, True])
def test_each_run_keeps_an_ocr_snapshot(factory, storage, uploaded, receipt_page, split):
    """Both single-process and two-stage runs store this run's OCR snapshot, which reads back
    exactly as recognized."""
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
    """A failure to store the snapshot is only logged; processing results are stored as usual."""
    from jacara.core.storage import StorageError

    put = storage.put

    def refuse(key, data, content_type):
        if key.startswith("ocr-snapshots/"):
            raise StorageError("满了")
        put(key, data, content_type)

    storage.put = refuse
    assert run(factory, storage, uploaded, FakeOcr(receipt_page)) == "done"
