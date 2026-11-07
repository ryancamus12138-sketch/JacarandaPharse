"""任务队列：API 把"处理这个文档"放进 Valkey，后台的 worker 取出来做。

API 这一边只负责"放进去"（enqueue），不导入流水线代码，所以启动快、不占内存。
真正干活的任务定义在 jacara/worker.py。两边靠任务名字（PROCESS_DOCUMENT）对上。
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Protocol

from celery import Celery

from jacara.core.config import get_settings

PROCESS_DOCUMENT = "jacara.process_document"
"""任务名字。API 按名字发送，worker 按名字认领。"""

QUEUE = "pipeline"
"""队列名字。以后 OCR、大模型、导出可以分成不同的队列，各开各的 worker。"""


class QueueError(Exception):
    """任务队列（Valkey）连不上。"""


class TaskQueue(Protocol):
    def enqueue_document(self, document_id: uuid.UUID) -> None:
        """把一个文档放进待处理队列。"""
        ...


def create_celery() -> Celery:
    settings = get_settings()
    app = Celery("jacara", broker=settings.valkey_url)
    app.conf.update(
        task_default_queue=QUEUE,
        task_ignore_result=True,  # 结果写在数据库里（documents.status），不用 Celery 再存一份
        task_acks_late=True,  # 做完才算"领走"：worker 中途崩溃，任务会重新回到队列
        worker_prefetch_multiplier=1,  # 一次只领一个任务：大模型一次只能处理一张
        broker_connection_retry_on_startup=True,
        # Valkey 没启动时，发送最多等几秒就报错，不让上传接口一直卡着
        broker_transport_options={"socket_connect_timeout": 2, "socket_timeout": 5},
        broker_connection_timeout=2,
    )
    return app


@lru_cache
def get_celery() -> Celery:
    return create_celery()


class CeleryQueue:
    """用 Celery + Valkey 实现的任务队列。"""

    def __init__(self, app: Celery | None = None) -> None:
        self.app = app or get_celery()

    def enqueue_document(self, document_id: uuid.UUID) -> None:
        try:
            # retry=False：连不上就马上报错，由调用方决定怎么办
            self.app.send_task(PROCESS_DOCUMENT, args=[str(document_id)], retry=False)
        except Exception as err:  # kombu 的连接错误种类很多，统一包成 QueueError
            raise QueueError("任务队列（Valkey）连不上") from err
