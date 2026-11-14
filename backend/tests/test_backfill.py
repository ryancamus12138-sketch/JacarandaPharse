"""Backfilling OCR results for documents processed by older versions: extracted values and revisions
are untouched; only extractions.ocr is added."""

from jacara.core.db import session_scope
from jacara.models import Document, Extraction
from jacara.pipeline.backfill import backfill_document, missing_ocr
from jacara.pipeline.store import save_document


def make_old_document(factory, storage, report, tmp_path):
    """A document from an older version: extraction results without OCR results, original file in
    object storage."""
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"jpeg")
    with session_scope(factory) as session:
        document = save_document(session, path, [report])
        document.storage_key = "documents/old/receipt.jpg"
        document.content_type = "image/jpeg"
        for extraction in document.extractions:
            extraction.ocr = None
        document_id = document.id
    storage.put("documents/old/receipt.jpg", b"jpeg", "image/jpeg")
    return document_id


def test_backfill_fills_only_the_ocr(factory, storage, report, receipt_page, tmp_path):
    document_id = make_old_document(factory, storage, report, tmp_path)
    assert missing_ocr(factory) == [document_id]

    seen = []

    def read(path):
        seen.append(path.suffix)
        return [receipt_page]

    assert backfill_document(document_id, storage=storage, read=read, factory=factory) == 1
    assert seen == [".jpg"]
    assert missing_ocr(factory) == []
    with session_scope(factory) as session:
        [extraction] = session.query(Extraction).all()
        assert extraction.ocr is not None
        assert extraction.ocr["lines"][0]["text"] == "BOOK TA _K (TAMAN DAYA) SDN BHD"
        values = {v.name: v.value for v in extraction.values}
        assert values["total"] == "9.0"  # Extraction results unchanged.


def test_backfill_twice_does_nothing(factory, storage, report, receipt_page, tmp_path):
    document_id = make_old_document(factory, storage, report, tmp_path)
    backfill_document(document_id, storage=storage, read=lambda p: [receipt_page], factory=factory)
    again = backfill_document(
        document_id, storage=storage, read=lambda p: [receipt_page], factory=factory
    )
    assert again == 0


def test_documents_without_a_file_are_skipped(factory, report, tmp_path):
    path = tmp_path / "cli.jpg"
    path.write_bytes(b"x")
    with session_scope(factory) as session:
        # Saved by the CLI, without an original file.
        document = save_document(session, path, [report])
        for extraction in document.extractions:
            extraction.ocr = None
    assert missing_ocr(factory) == []
    with session_scope(factory) as session:
        assert session.query(Document).count() == 1
