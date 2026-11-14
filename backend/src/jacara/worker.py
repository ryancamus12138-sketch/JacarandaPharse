"""Background worker: takes tasks from the queue and processes uploaded documents.

Start: uv run jacara worker (the API and the model server must be running)

The worker is a separate process from the API: the API returns immediately after an upload and
processing happens here. OCR models and the LLM client are loaded on the first task and reused
afterwards.

The jobs (process_document_job, ...) do not depend on the queue implementation. JACARA_QUEUE
decides whether they are consumed from Celery (Valkey) or from Huey (queue.db); see
core/queue.py."""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Literal

from jacara.core.config import get_settings
from jacara.core.queue import (
    EVAL_OCR,
    EXTRACT_DOCUMENT,
    EXTRACT_QUEUE,
    PROCESS_DOCUMENT,
    QUEUE,
    create_queue,
)
from jacara.core.storage import get_storage
from jacara.pipeline.process import (
    Outcome,
    extract_document,
    process_document,
    read_document,
    reset_interrupted,
)
from jacara.pipeline.run import Pipeline

log = logging.getLogger(__name__)


@lru_cache
def get_pipeline() -> Pipeline:
    """Assemble the pipeline. Loading OCR models takes several seconds, so this happens only
    once."""
    from jacara.core.config import model_api_key
    from jacara.plugins.extractors import LlmExtractor
    from jacara.plugins.model_backends import ModelConfig, create_model_backend
    from jacara.plugins.ocr import create_ocr_engine

    config = ModelConfig(base_url=get_settings().model_server, api_key=model_api_key())
    backend = create_model_backend(config)
    from jacara.structure.engines import default_engines

    return Pipeline(
        ocr=create_ocr_engine(),
        extractor=LlmExtractor(backend),
        structure=default_engines(),
        vision=get_settings().jacara_vision,
        pdf_read=get_settings().jacara_pdf_read,
    )


Stage = Literal["all", "read", "extract"]
"""Stage handled by this worker: all (end to end); read (OCR stage); extract (model stage)."""

STAGE: Stage = "all"


# ---- Jobs: identical for every queue implementation ----


def process_document_job(document_id: str) -> str:
    """Process one document. Arguments are strings because tasks are serialized in the queue.

    The OCR stage only runs OCR and hands off to the model stage; without stages the document
    is processed end to end."""
    if STAGE == "read":
        return read_document(
            uuid.UUID(document_id),
            storage=get_storage(),
            pipeline=get_pipeline(),
            send=create_queue().enqueue_extract,
        )
    return process_document(uuid.UUID(document_id), storage=get_storage(), pipeline=get_pipeline())


def extract_document_job(document_id: str, attempt: str) -> Outcome:
    """Model stage: extract a document already OCR'd by the OCR stage."""
    return extract_document(
        uuid.UUID(document_id), attempt, storage=get_storage(), pipeline=get_pipeline()
    )


def eval_ocr_job(run_id: str) -> str:
    """OCR model evaluation: runs on the OCR stage; each arm creates an OCR engine from its own
    model files."""
    from jacara.core.db import get_session_factory
    from jacara.modules.hub.evals import run_eval

    return run_eval(uuid.UUID(run_id), get_session_factory(), get_storage())


def _reset_on_start() -> None:
    # With stages, `jacara start` does this once before starting both stages: if one stage started
    # working before the other started and ran this, documents in progress would be treated as
    # interrupted.
    if STAGE != "all":
        return
    count = reset_interrupted()
    if count:
        log.warning("%d 个文档上次处理到一半被中断，已标记为失败，可以重新处理", count)


# ---- Consumers ----


def run_worker(stage: Stage = "all") -> None:
    """Start the worker; stop with Ctrl + C.

        stage: all runs everything in one process; read consumes uploaded documents and only runs
    OCR; extract consumes OCR'd documents and calls the model. `jacara start` runs read and extract
    by default.

        One task at a time in one process: the local LLM answers one request at a time, so more
    workers would only queue; moreover, onnxruntime (used by OCR) tends to crash in forked child
    processes on macOS."""
    global STAGE
    STAGE = stage
    if get_settings().jacara_queue == "huey":
        _run_huey(stage)
    else:
        _run_celery(stage)


def _run_huey(stage: Stage) -> None:
    """Huey consumer with one worker thread (worker_type="thread", workers=1), the equivalent of
    Celery's --pool=solo --concurrency=1. The all stage consumes the OCR-stage queue only: without
    stages no document is handed to the model-stage queue."""
    from jacara.core.queue import get_hueys, huey_tasks

    huey_tasks()  # Register the tasks before the consumer starts.
    name = EXTRACT_QUEUE if stage == "extract" else QUEUE
    _reset_on_start()
    get_hueys()[name].create_consumer(workers=1, worker_type="thread").run()


def _run_celery(stage: Stage) -> None:
    from celery.signals import worker_ready

    from jacara.core.queue import get_celery

    app = get_celery()
    app.task(name=PROCESS_DOCUMENT)(process_document_job)
    app.task(name=EXTRACT_DOCUMENT)(extract_document_job)
    app.task(name=EVAL_OCR)(eval_ocr_job)
    worker_ready.connect(lambda **_: _reset_on_start(), weak=False)
    queues = {"all": f"{QUEUE},{EXTRACT_QUEUE}", "read": QUEUE, "extract": EXTRACT_QUEUE}[stage]
    name = "jacara" if stage == "all" else f"jacara-{stage}"
    app.worker_main(
        [
            "worker",
            "--pool=solo",
            "--concurrency=1",
            f"--queues={queues}",
            "--loglevel=INFO",
            f"--hostname={name}@%h",
            # Single worker: no need to coordinate with other workers (saves the 1-second wait at
            # startup).
            "--without-mingle",
            "--without-gossip",
        ]
    )
