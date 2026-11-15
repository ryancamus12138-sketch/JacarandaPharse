"""Task queue: the API puts "process this document" on the queue and a background worker picks it
up.

Two implementations, selected by JACARA_QUEUE:
- CeleryQueue: Celery with Valkey, for the server deployment (docker compose).
- HueyQueue: Huey with its SQLite storage (queue.db in the data directory), for the desktop
  edition, which runs no separate queue service.

The API side only enqueues and does not import pipeline code, so it starts quickly and stays
small. The work itself lives in jacara/worker.py; both sides agree on task names
(PROCESS_DOCUMENT, ...)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol

from jacara.core.config import data_home, get_settings
from jacara.core.i18n import tr

if TYPE_CHECKING:
    from celery import Celery
    from huey import SqliteHuey

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

QUEUE_FILE = "queue.db"
"""SQLite file of the Huey queue in the data directory. Kept apart from the business database so
that queue polling never waits for a business write lock."""


class QueueError(Exception):
    """The task queue cannot be reached."""


class TaskQueue(Protocol):
    def enqueue_document(self, document_id: uuid.UUID) -> None:
        """Enqueue a document for processing."""
        ...

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        """Enqueue an OCR model evaluation."""
        ...


# ---- Celery (server deployment) ----


def create_celery() -> Celery:
    from celery import Celery

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
            raise QueueError(tr("无法连接任务队列（Valkey）")) from err

    def enqueue_document(self, document_id: uuid.UUID) -> None:
        try:
            # retry=False: fail immediately when unreachable and let the caller decide.
            self.app.send_task(PROCESS_DOCUMENT, args=[str(document_id)], retry=False)
        # kombu raises many connection error types; wrap them all in QueueError.
        except Exception as err:
            raise QueueError(tr("无法连接任务队列（Valkey）")) from err

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        try:
            self.app.send_task(EVAL_OCR, args=[str(run_id)], queue=QUEUE, retry=False)
        except Exception as err:
            raise QueueError(tr("无法连接任务队列（Valkey）")) from err


# ---- Huey (desktop edition) ----


@lru_cache
def get_hueys() -> dict[str, SqliteHuey]:
    """One Huey instance per queue, all stored in queue.db. A Huey instance is one named queue in
    its storage, so the OCR stage and the model stage consume separate queues as with Celery.

    results=False: results are stored in the database (documents.status), as with Celery's
    task_ignore_result."""
    from huey import SqliteHuey

    from jacara.core.db import SQLITE_TIMEOUT

    path = data_home() / QUEUE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return {
        name: SqliteHuey(name=name, filename=str(path), results=False, timeout=SQLITE_TIMEOUT)
        for name in (QUEUE, EXTRACT_QUEUE)
    }


@lru_cache
def huey_tasks() -> dict[str, Callable[..., Any]]:
    """Register the tasks on the Huey instances. Calling a returned task enqueues it. The task
    bodies import the worker code only when they run, so the API never loads the pipeline."""
    hueys = get_hueys()

    @hueys[QUEUE].task(name=PROCESS_DOCUMENT)
    def process_document(document_id: str) -> None:
        from jacara.worker import process_document_job

        process_document_job(document_id)

    @hueys[EXTRACT_QUEUE].task(name=EXTRACT_DOCUMENT)
    def extract_document(document_id: str, attempt: str) -> None:
        from jacara.worker import extract_document_job

        extract_document_job(document_id, attempt)

    @hueys[QUEUE].task(name=EVAL_OCR)
    def eval_ocr(run_id: str) -> None:
        from jacara.worker import eval_ocr_job

        eval_ocr_job(run_id)

    return {
        PROCESS_DOCUMENT: process_document,
        EXTRACT_DOCUMENT: extract_document,
        EVAL_OCR: eval_ocr,
    }


class HueyQueue:
    """Task queue backed by Huey's SQLite storage."""

    def _send(self, task: str, *args: str) -> None:
        try:
            huey_tasks()[task](*args)
        except Exception as err:
            raise QueueError(tr("无法写入任务队列：{err}", err=err)) from err

    def enqueue_extract(self, document_id: uuid.UUID, attempt: str) -> None:
        """Hand a document from the OCR stage to the model stage after OCR."""
        self._send(EXTRACT_DOCUMENT, str(document_id), attempt)

    def enqueue_document(self, document_id: uuid.UUID) -> None:
        self._send(PROCESS_DOCUMENT, str(document_id))

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        self._send(EVAL_OCR, str(run_id))


def create_queue() -> CeleryQueue | HueyQueue:
    """The task queue selected by JACARA_QUEUE."""
    return HueyQueue() if get_settings().jacara_queue == "huey" else CeleryQueue()
