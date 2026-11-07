"""后台处理一个上传的文档：从对象存储取出文件 → 跑流水线 → 保存结果，并更新状态。

状态变化：uploaded → processing → done / failed

每一步各用一个短事务，而不是一个大事务包住整个处理过程：
处理一张收据要十几秒，期间"processing"状态要能被 API 查到，所以认领之后马上提交。
"""

from __future__ import annotations

import logging
import mimetypes
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import session_scope
from jacara.core.storage import ObjectStorage, StorageError
from jacara.models import Document
from jacara.pipeline.run import Pipeline
from jacara.pipeline.store import add_extractions
from jacara.plugins.extractors import ExtractionError
from jacara.plugins.model_backends import ModelBackendError
from jacara.plugins.ocr.loader import UnsupportedFileError

log = logging.getLogger(__name__)

Outcome = Literal["done", "failed", "skipped"]


def process_document(
    document_id: uuid.UUID,
    *,
    storage: ObjectStorage,
    pipeline: Pipeline,
    factory: sessionmaker[Session] | None = None,
) -> Outcome:
    """处理一个文档。返回结果，不抛出异常：出错时把原因写进数据库，状态变成 failed。"""
    if not _claim(document_id, factory):
        # 已经被处理过、正在处理、或者文档不存在。同一个任务被重复投递时也会走到这里。
        log.info("跳过 %s：状态不是 uploaded", document_id)
        return "skipped"

    try:
        with session_scope(factory) as session:
            document = session.get_one(Document, document_id)
            key, filename, content_type = (
                document.storage_key,
                document.filename,
                document.content_type,
            )
        if key is None:
            raise StorageError("没有原始文件")

        data = storage.get(key)
        with tempfile.TemporaryDirectory() as tmp:
            # 流水线按后缀判断文件类型，所以临时文件的后缀按 content_type 取
            path = Path(tmp) / f"original{_suffix(filename, content_type)}"
            path.write_bytes(data)
            reports = pipeline.run(path, name=filename)

        with session_scope(factory) as session:
            document = session.get_one(Document, document_id)
            add_extractions(session, document, reports)
            document.page_count = len(reports)
            document.status = "done"
        log.info("完成 %s：%d 页", document_id, len(reports))
        return "done"

    except Exception as err:
        log.exception("处理失败 %s", document_id)  # 完整的错误堆栈写进 worker 的日志
        with session_scope(factory) as session:
            document = session.get_one(Document, document_id)
            document.status = "failed"
            document.error = describe_error(err)
        return "failed"


def _claim(document_id: uuid.UUID, factory: sessionmaker[Session] | None) -> bool:
    """把状态从 uploaded 改成 processing。

    一条 UPDATE 同时完成"检查 + 修改"，两个 worker 不会抢到同一个文档。
    """
    with session_scope(factory) as session:
        claimed = session.scalar(
            update(Document)
            .where(Document.id == document_id, Document.status == "uploaded")
            .values(status="processing", error=None)
            .returning(Document.id)
        )
    return claimed is not None


def reset_interrupted(factory: sessionmaker[Session] | None = None) -> int:
    """worker 启动时调用：上次被中途关掉、停在 processing 的文档，改成 failed，可以重新处理。

    只适合只开一个 worker 的情况（个人模式）。多个 worker 时要改成按超时判断。
    """
    with session_scope(factory) as session:
        ids = session.scalars(
            update(Document)
            .where(Document.status == "processing")
            .values(status="failed", error="处理到一半时 worker 被关闭了，请重新处理")
            .returning(Document.id)
        ).all()
    return len(ids)


def describe_error(err: Exception) -> str:
    """把异常变成给人看的一句话。技术细节在 worker 日志里。"""
    if isinstance(err, ModelBackendError | ExtractionError | UnsupportedFileError):
        return str(err)  # 这几种错误本身就是写给人看的说明
    if isinstance(err, StorageError):
        return "对象存储里取不到原始文件"
    return f"处理时出现意外错误（{type(err).__name__}），详情见 worker 日志"


def _suffix(filename: str, content_type: str | None) -> str:
    guessed = mimetypes.guess_extension(content_type) if content_type else None
    return guessed or Path(filename).suffix
