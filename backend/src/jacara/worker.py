"""后台 worker：从 Valkey 队列里取任务，处理上传的文档。

启动：uv run jacara worker（要先启动 docker compose 和 jacara serve）

worker 是一个单独的进程，和 API 分开：API 收到上传马上返回，处理慢慢在这里做。
OCR 模型和大模型客户端在第一次处理时加载，之后一直复用。
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Any

from celery.signals import worker_ready

from jacara.core.config import get_settings
from jacara.core.queue import PROCESS_DOCUMENT, QUEUE, get_celery
from jacara.core.storage import get_storage
from jacara.pipeline.process import Outcome, process_document, reset_interrupted
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
    )


@celery_app.task(name=PROCESS_DOCUMENT)
def process_document_task(document_id: str) -> Outcome:
    """队列里的一个任务：处理一个文档。参数用字符串，因为任务要先变成 JSON 放进 Valkey。"""
    return process_document(uuid.UUID(document_id), storage=get_storage(), pipeline=get_pipeline())


@worker_ready.connect
def _on_ready(**_: Any) -> None:
    count = reset_interrupted()
    if count:
        log.warning("%d 个文档上次处理到一半被中断，已标记为失败，可以重新处理", count)


def run_worker() -> None:
    """启动 worker，Ctrl + C 停止。

    --pool=solo：只用一个进程、一次处理一个文档。
    本地大模型一次只能回答一个请求，开多个进程也只是排队；
    而且 macOS 上 OCR 用的 onnxruntime 在"复制出来的子进程"里容易崩溃，solo 模式没有这个问题。
    """
    celery_app.worker_main(
        [
            "worker",
            "--pool=solo",
            "--concurrency=1",
            f"--queues={QUEUE}",
            "--loglevel=INFO",
            "--hostname=jacara@%h",
            # 只有一个 worker，不用和别的 worker 互相打招呼（省掉启动时等的 1 秒）
            "--without-mingle",
            "--without-gossip",
        ]
    )
