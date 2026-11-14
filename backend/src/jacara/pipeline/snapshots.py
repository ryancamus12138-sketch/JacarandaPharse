"""OCR snapshot of each run: the complete OCR output (four corners, confidence, engine and models,
PDF text layer, form fields; nothing dropped) stored in object storage, one file per run.

Why store it:
- Replay (`jacara eval replay`): after changing model, prompt or template, extract again from the
  same OCR output, so differences come only from extraction and not from OCR changes;
- Error attribution: whether a value appears in the OCR text can be read from the snapshot
  without re-running OCR.

The database also stores OCR results (Extraction.ocr), but those serve the review page and are
split per document with converted coordinates; the snapshot covers the whole file exactly as
recognized."""

from __future__ import annotations

import logging
import uuid

from jacara.core.storage import ObjectStorage, StorageError
from jacara.pipeline.handoff import dump_pages, load_pages
from jacara.plugins.ocr import OcrPage

log = logging.getLogger(__name__)


def snapshot_key(document_id: uuid.UUID, run: int) -> str:
    """Object storage key of the OCR snapshot of a run."""
    return f"ocr-snapshots/{document_id}/run-{run}.json"


def save_snapshot(
    storage: ObjectStorage, document_id: uuid.UUID, run: int, pages: list[OcrPage]
) -> str | None:
    """Store this run's OCR snapshot and return its key. On failure, only log and return None:
    processing results are unaffected."""
    key = snapshot_key(document_id, run)
    try:
        storage.put(key, dump_pages(pages), "application/json")
    except StorageError:
        log.warning("OCR 快照 %s 没存进去", key)
        return None
    return key


def load_snapshot(storage: ObjectStorage, key: str) -> list[OcrPage]:
    """Read back a run's OCR snapshot, exactly as recognized."""
    return load_pages(storage.get(key))
