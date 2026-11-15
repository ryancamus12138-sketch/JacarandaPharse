"""Add OCR results to documents processed by older versions without re-uploading.

Review tools need the OCR result of each page (extractions.ocr), which older documents lack. This
module re-reads the original file and re-runs OCR, adding only the OCR result: extracted values,
evidence and manual reviews are untouched. OCR is deterministic: the same image and model produce
the same text and positions, so evidence boxes still match."""

from __future__ import annotations

import logging
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import session_scope
from jacara.core.i18n import stored
from jacara.core.storage import ObjectStorage
from jacara.models import Document, Extraction
from jacara.pipeline.process import suffix_for
from jacara.pipeline.store import ocr_json
from jacara.plugins.ocr.base import OcrPage

log = logging.getLogger(__name__)

ReadPages = Callable[[Path], list[OcrPage]]
"""Read every page of a file (images through OCR, PDFs with a text layer directly). Pipeline.read in
real runs."""


def missing_ocr(factory: sessionmaker[Session] | None = None) -> list[uuid.UUID]:
    """Ids of documents without OCR results but with an original file."""
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
    """Backfill one document and return the number of pages filled. Raises ValueError if the page
    count does not match (file replaced?)."""
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
                raise ValueError(
                    stored(
                        "{filename}：文件中不存在第 {page} 页",
                        filename=filename,
                        page=extraction.page,
                    )
                )
            extraction.ocr = ocr_json(pages[extraction.page - 1])
            filled += 1
    log.info("补上 OCR 结果 %s：%d 页", document_id, filled)
    return filled
