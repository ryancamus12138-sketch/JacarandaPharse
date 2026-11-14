"""Background worker: takes tasks from the Valkey queue and processes uploaded documents.

Start: uv run jacara worker (docker compose and `jacara serve` must be running)

The worker is a separate process from the API: the API returns immediately after an upload and
processing happens here. OCR models and the LLM client are loaded on the first task and reused
afterwards."""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Any, Literal

from celery.signals import worker_ready

from jacara.core.config import get_settings
from jacara.core.queue import (
    EVAL_OCR,
    EXTRACT_DOCUMENT,
    EXTRACT_QUEUE,
    PROCESS_DOCUMENT,
    QUEUE,
    CeleryQueue,
    get_celery,
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

celery_app = get_celery()


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


@celery_app.task(name=PROCESS_DOCUMENT)
def process_document_task(document_id: str) -> str:
    """A queued task: process one document. Arguments are strings because tasks are serialized to
    JSON in Valkey.

        The OCR stage only runs OCR and hands off to the model stage; without stages the document
    is processed end to end."""
    if STAGE == "read":
        return read_document(
            uuid.UUID(document_id),
            storage=get_storage(),
            pipeline=get_pipeline(),
            send=CeleryQueue(celery_app).enqueue_extract,
        )
    return process_document(uuid.UUID(document_id), storage=get_storage(), pipeline=get_pipeline())


@celery_app.task(name=EXTRACT_DOCUMENT)
def extract_document_task(document_id: str, attempt: str) -> Outcome:
    """Model stage task: extract a document already OCR'd by the OCR stage."""
    return extract_document(
        uuid.UUID(document_id), attempt, storage=get_storage(), pipeline=get_pipeline()
    )


@celery_app.task(name=EVAL_OCR)
def eval_ocr_task(run_id: str) -> str:
    """OCR model evaluation: runs on the OCR stage; each arm creates an OCR engine from its own
    model files."""
    from jacara.core.db import get_session_factory
    from jacara.modules.hub.evals import run_eval

    return run_eval(uuid.UUID(run_id), get_session_factory(), get_storage())


@worker_ready.connect
def _on_ready(**_: Any) -> None:
    # With stages, `jacara start` does this once before starting both stages: if one stage started
    # working before the other started and ran this, documents in progress would be treated as
    # interrupted.
    if STAGE != "all":
        return
    count = reset_interrupted()
    if count:
        log.warning("%d 个文档上次处理到一半被中断，已标记为失败，可以重新处理", count)


def run_worker(stage: Stage = "all") -> None:
    """Start the worker; stop with Ctrl + C.

        stage: all runs everything in one process (consuming both queues); read consumes uploaded
    documents and only runs OCR; extract consumes OCR'd documents and calls the model. `jacara
    start` runs read and extract by default.

        --pool=solo: one process, one document at a time. The local LLM answers one request at a
    time, so more processes would only queue; moreover, onnxruntime (used by OCR) tends to crash
    in forked child processes on macOS, which solo mode avoids."""
    global STAGE
    STAGE = stage
    queues = {"all": f"{QUEUE},{EXTRACT_QUEUE}", "read": QUEUE, "extract": EXTRACT_QUEUE}[stage]
    name = "jacara" if stage == "all" else f"jacara-{stage}"
    celery_app.worker_main(
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
