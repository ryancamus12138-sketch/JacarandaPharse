"""后台 worker：从 Valkey 队列里取任务，处理上传的文档。

启动：uv run jacara worker（要先启动 docker compose 和 jacara serve）

worker 是一个单独的进程，和 API 分开：API 收到上传马上返回，处理慢慢在这里做。
OCR 模型和大模型客户端在第一次处理时加载，之后一直复用。
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Any, Literal

from celery.signals import worker_ready

from jacara.core.config import get_settings
from jacara.core.queue import (
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
    """组装流水线。加载 OCR 模型要几秒，所以只做一次。"""
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
"""这个 worker 做哪一段（ADR 0051）：all 从头做到尾；read 是 OCR 工位；extract 是模型工位。"""

STAGE: Stage = "all"


@celery_app.task(name=PROCESS_DOCUMENT)
def process_document_task(document_id: str) -> str:
    """队列里的一个任务：处理一个文档。参数用字符串，因为任务要先变成 JSON 放进 Valkey。

    OCR 工位只认字，认好交给模型工位；不分工位时从头做到尾。
    """
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
    """模型工位的任务：抽取一个 OCR 工位认好字的文档。"""
    return extract_document(
        uuid.UUID(document_id), attempt, storage=get_storage(), pipeline=get_pipeline()
    )


@worker_ready.connect
def _on_ready(**_: Any) -> None:
    # 分工位时由 jacara start 在启动两个工位之前统一做：一个工位先起来、开始干活了，
    # 另一个才起来再做，会把正在处理的也当成"中断了"
    if STAGE != "all":
        return
    count = reset_interrupted()
    if count:
        log.warning("%d 个文档上次处理到一半被中断，已标记为失败，可以重新处理", count)


def run_worker(stage: Stage = "all") -> None:
    """启动 worker，Ctrl + C 停止。

    stage（ADR 0051）：all 一个进程从头做到尾（两个队列都领）；read 只领上传的文档、只认字；
    extract 只领认好字的、调模型抽取。jacara start 默认开 read 和 extract 两个。

    --pool=solo：只用一个进程、一次处理一个文档。
    本地大模型一次只能回答一个请求，开多个进程也只是排队；
    而且 macOS 上 OCR 用的 onnxruntime 在"复制出来的子进程"里容易崩溃，solo 模式没有这个问题。
    """
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
            # 只有一个 worker，不用和别的 worker 互相打招呼（省掉启动时等的 1 秒）
            "--without-mingle",
            "--without-gossip",
        ]
    )
