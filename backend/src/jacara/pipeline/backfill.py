"""给更新之前处理的文档补上 OCR 结果，不用重新上传。

审核工具（ADR 0027）要用每一页的 OCR 结果（extractions.ocr），之前处理的文档没有存。
这里重新读一遍原始文件、重新 OCR，只把 OCR 结果补进去：抽取的值、出处、人工审核记录都不动。
OCR 是确定的：同一张图、同一个模型，认出的文字和位置和当初一样，出处框也就对得上。
"""

from __future__ import annotations

import logging
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import session_scope
from jacara.core.storage import ObjectStorage
from jacara.models import Document, Extraction
from jacara.pipeline.process import suffix_for
from jacara.pipeline.store import ocr_json
from jacara.plugins.ocr.base import OcrPage

log = logging.getLogger(__name__)

ReadPages = Callable[[Path], list[OcrPage]]
"""读一个文件的每一页（图片走 OCR，PDF 有文字层直接读）。真跑时是 Pipeline.read。"""


def missing_ocr(factory: sessionmaker[Session] | None = None) -> list[uuid.UUID]:
    """还没有 OCR 结果、又有原始文件的文档 id。"""
    with session_scope(factory) as session:
        query = (
            select(Document.id)
            .join(Extraction, Extraction.document_id == Document.id)
            .where(Extraction.ocr.is_(None), Document.storage_key.is_not(None))
            .distinct()
        )
        return list(session.scalars(query))


def backfill_document(
    document_id: uuid.UUID,
    *,
    storage: ObjectStorage,
    read: ReadPages,
    factory: sessionmaker[Session] | None = None,
) -> int:
    """补一个文档：返回补了几页。页数对不上（文件换了？）就不补，抛 ValueError。"""
    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        key, filename, content_type = document.storage_key, document.filename, document.content_type
    if key is None:
        return 0
    data = storage.get(key)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"original{suffix_for(filename, content_type)}"
        path.write_bytes(data)
        pages = read(path)

    filled = 0
    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        for extraction in document.extractions:
            if extraction.ocr is not None or extraction.last_page:
                continue
            if not 1 <= extraction.page <= len(pages):
                raise ValueError(f"{filename}：第 {extraction.page} 页在文件里找不到")
            extraction.ocr = ocr_json(pages[extraction.page - 1])
            filled += 1
    log.info("补上 OCR 结果 %s：%d 页", document_id, filled)
    return filled
