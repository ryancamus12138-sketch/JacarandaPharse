"""每一轮的 OCR 快照（ADR 0053）：OCR 认出来的原样（四个角、置信度、引擎和模型、PDF 文字层、
表单字段，一样不丢）存进对象存储，一轮一个文件。

为什么要存：
- 回放（jacara eval replay）：换了模型、提示词、模板，用同一份 OCR 结果再抽一次，
  两边的差别只来自抽取，不混进 OCR 的变化；
- 错误归因：值在不在 OCR 认出来的字里，看快照就知道，不用重新认。

数据库里也存了 OCR 结果（Extraction.ocr），但那是给审核页用的，按单据切开、坐标换过；
快照是整份文件、和刚认出来时一模一样的。
"""

from __future__ import annotations

import logging
import uuid

from jacara.core.storage import ObjectStorage, StorageError
from jacara.pipeline.handoff import dump_pages, load_pages
from jacara.plugins.ocr import OcrPage

log = logging.getLogger(__name__)


def snapshot_key(document_id: uuid.UUID, run: int) -> str:
    """第几轮的 OCR 快照放在对象存储的哪里。"""
    return f"ocr-snapshots/{document_id}/run-{run}.json"


def save_snapshot(
    storage: ObjectStorage, document_id: uuid.UUID, run: int, pages: list[OcrPage]
) -> str | None:
    """存下这一轮的 OCR 快照，返回存在哪里。存不进去只记日志、返回 None：不影响处理结果。"""
    key = snapshot_key(document_id, run)
    try:
        storage.put(key, dump_pages(pages), "application/json")
    except StorageError:
        log.warning("OCR 快照 %s 没存进去", key)
        return None
    return key


def load_snapshot(storage: ObjectStorage, key: str) -> list[OcrPage]:
    """读回一轮的 OCR 快照，和刚认出来时一模一样。"""
    return load_pages(storage.get(key))
