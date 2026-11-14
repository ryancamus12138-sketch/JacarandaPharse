"""Stopping and restarting a document while it is queued or being processed: the running worker
notices through its claim token, writes nothing and gives the document up."""

from __future__ import annotations

import uuid

import pytest

from jacara.core.db import session_scope
from jacara.core.i18n import render
from jacara.models import Document, Extraction
from jacara.pipeline.process import Superseded, _claim, _fail, _report
from jacara.pipeline.run import Pipeline
from jacara.plugins.extractors import LlmExtractor

from .test_process import Sent
from .test_template_library import RECEIPT, FakeOcr, SchemaBackend, process, upload


class UserActsDuringModel(SchemaBackend):
    """A model call during which the user presses a button in the web page."""

    def __init__(self, act) -> None:
        super().__init__(RECEIPT)
        self.act = act

    def chat(self, messages, *, json_schema=None):
        self.act()
        return super().chat(messages, json_schema=json_schema)


def extractions(factory, document_id) -> int:
    with session_scope(factory) as session:
        return session.query(Extraction).filter_by(document_id=uuid.UUID(document_id)).count()


def state(factory, document_id) -> Document:
    with session_scope(factory) as session:
        return session.get_one(Document, uuid.UUID(document_id))


def test_stop_while_the_model_writes(client, factory, storage, queue, receipt_page):
    document_id = upload(client)

    def stop() -> None:
        response = client.post(f"/api/v1/documents/{document_id}/stop")
        assert response.status_code == 200, response.text

    result = process(factory, storage, document_id, receipt_page, UserActsDuringModel(stop))
    assert result == "skipped"
    document = state(factory, document_id)
    assert document.status == "failed" and document.claim is None
    assert render(document.error or "") == "已手动停止处理"
    assert extractions(factory, document_id) == 0  # Nothing from the stopped run was saved.
    # A stopped document is reprocessed like any failed one.
    response = client.post(
        f"/api/v1/documents/{document_id}/reprocess", json={"reason": "other", "note": "重试"}
    )
    assert response.status_code == 202, response.text
    assert process(factory, storage, document_id, receipt_page) == "done"
    assert extractions(factory, document_id) == 1


def test_restart_while_the_model_writes(client, factory, storage, queue, receipt_page):
    document_id = upload(client)

    def restart() -> None:
        response = client.post(
            f"/api/v1/documents/{document_id}/reprocess", json={"reason": "other", "note": "太慢"}
        )
        assert response.status_code == 202, response.text

    result = process(factory, storage, document_id, receipt_page, UserActsDuringModel(restart))
    assert result == "skipped"
    queued = state(factory, document_id)
    assert queued.status == "uploaded"  # Queued again,
    assert queued.stage is None and queued.progress is None  # without the old run's progress.
    assert queue.sent.count(uuid.UUID(document_id)) == 2
    assert extractions(factory, document_id) == 0
    assert process(factory, storage, document_id, receipt_page) == "done"
    assert state(factory, document_id).status == "done"


def test_a_stopped_claim_can_no_longer_write(client, factory, storage):
    document_id = uuid.UUID(upload(client))
    claim = _claim(document_id, factory)
    assert claim is not None
    _report(document_id, "文字识别", 5, factory, claim)
    assert client.post(f"/api/v1/documents/{document_id}/stop").status_code == 200
    with pytest.raises(Superseded):
        _report(document_id, "文字识别", 50, factory, claim)
    _fail(document_id, RuntimeError("late error"), factory, claim)  # Ignored: not its document.
    assert render(state(factory, str(document_id)).error or "") == "已手动停止处理"


def test_stop_between_the_two_stages(client, factory, storage, receipt_page):
    from jacara.pipeline.process import extract_document, read_document

    document_id = uuid.UUID(upload(client))
    pipeline = Pipeline(ocr=FakeOcr(receipt_page), extractor=LlmExtractor(SchemaBackend(RECEIPT)))
    sent = Sent()
    assert (
        read_document(document_id, storage=storage, pipeline=pipeline, send=sent, factory=factory)
        == "read"
    )
    [(_, attempt)] = sent.items
    assert client.post(f"/api/v1/documents/{document_id}/stop").status_code == 200
    result = extract_document(
        document_id, attempt, storage=storage, pipeline=pipeline, factory=factory
    )
    assert result == "skipped"
    assert not any(k.startswith("pipeline/") for k in storage.files)  # Hand-off removed.


def test_only_queued_or_running_documents_can_be_stopped(client, factory, storage, receipt_page):
    document_id = upload(client)
    process(factory, storage, document_id, receipt_page)
    response = client.post(f"/api/v1/documents/{document_id}/stop")
    assert response.status_code == 409
