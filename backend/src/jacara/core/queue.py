"""Task queue: the API puts "process this document" on Valkey and the background worker picks it up.

The API side only enqueues and does not import pipeline code, so it starts quickly and stays
small. The task implementations live in jacara/worker.py; both sides agree on task names
(PROCESS_DOCUMENT)."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Protocol

from celery import Celery

from jacara.core.config import get_settings
from jacara.core.i18n import tr

PROCESS_DOCUMENT = "jacara.process_document"
"""Task name. The API sends by name; the worker claims by name."""

QUEUE = "pipeline"
"""Queue name for uploaded documents. With the two-stage pipeline, consumed by the OCR stage."""

EXTRACT_DOCUMENT = "jacara.extract_document"
EXTRACT_QUEUE = "extract"
"""Second pipeline stage: after OCR, the OCR stage puts "extract this document" on this queue for
the model stage."""


EVAL_OCR = "jacara.eval_ocr"
"""OCR model evaluation: routed to the OCR stage queue (QUEUE), whose workers already load OCR."""


class QueueError(Exception):
    """The task queue (Valkey) is unreachable."""


class TaskQueue(Protocol):
    def enqueue_document(self, document_id: uuid.UUID) -> None:
        """Enqueue a document for processing."""
        ...

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        """Enqueue an OCR model evaluation."""
        ...


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("jacara", broker=settings.valkey_url)
    app.conf.update(
        task_default_queue=QUEUE,
        # Results are stored in the database (documents.status); no Celery result backend.
        task_ignore_result=True,
        # Acknowledge after completion: if the worker crashes mid-task, the task returns to the
        # queue.
        task_acks_late=True,
        # Prefetch one task at a time: the LLM processes one document at a time.
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        # When Valkey is down, fail after a few seconds instead of blocking the upload endpoint.
        broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 5},
        broker_connection_timeout=2,
    )
    return app


@lru_cache
def get_celery() -> Celery:
    return create_celery()


class CeleryQueue:
    """Task queue backed by Celery and Valkey."""

    def __init__(self, app: Celery | None = None) -> None:
        self.app = app or get_celery()

    def enqueue_extract(self, document_id: uuid.UUID, attempt: str) -> None:
        """Hand a document from the OCR stage to the model stage after OCR."""
        try:
            self.app.send_task(
                EXTRACT_DOCUMENT, args=[str(document_id), attempt], queue=EXTRACT_QUEUE, retry=False
            )
        except Exception as err:
            raise QueueError(tr("任务队列（Valkey）连不上")) from err

    def enqueue_document(self, document_id: uuid.UUID) -> None:
        try:
            # retry=False: fail immediately when unreachable and let the caller decide.
            self.app.send_task(PROCESS_DOCUMENT, args=[str(document_id)], retry=False)
        # kombu raises many connection error types; wrap them all in QueueError.
        except Exception as err:
            raise QueueError(tr("任务队列（Valkey）连不上")) from err

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        try:
            self.app.send_task(EVAL_OCR, args=[str(run_id)], queue=QUEUE, retry=False)
        except Exception as err:
            raise QueueError(tr("任务队列（Valkey）连不上")) from err
