"""Storing in the database, tested with in-memory SQLite; no PostgreSQL required.

SQLite is a small embedded database that ships with Python. The schema is generated from the same
models, so the store-and-load logic can be tested. PostgreSQL specifics (JSONB and others) are
covered by migrations and doctor."""

import pytest
from sqlalchemy import select

from jacara.core.db import session_scope
from jacara.core.owner import PERSONAL_OWNER
from jacara.models import Document, ExtractedValue, Extraction
from jacara.pipeline.store import file_sha256, save_document


@pytest.fixture
def receipt_file(tmp_path):
    path = tmp_path / "receipt.jpg"
    path.write_bytes(b"not really a jpeg")
    return path


def test_save_and_read_back(factory, report, receipt_file):
    with session_scope(factory) as session:
        document_id = save_document(session, receipt_file, [report]).id

    with session_scope(factory) as session:
        document = session.get_one(Document, document_id)
        assert document.filename == "receipt.jpg"
        assert document.sha256 == file_sha256(receipt_file)
        assert document.user_id == PERSONAL_OWNER.user_id  # Data carries ownership.

        [extraction] = document.extractions
        assert extraction.model == "test-model"  # Provenance.
        assert extraction.prompt_version == "extract-v2"
        assert extraction.raw_output == '{"raw": "模型原话"}'  # Raw answer.
        assert extraction.checks["issues"][0]["rule"] == "change"

        values = {v.name: v for v in extraction.values}
        assert [v.name for v in extraction.values] == [
            "merchant",
            "total",
            "cash",
            "change",
            "member",
        ]
        assert values["total"].value == "9.0"
        assert values["total"].evidence is not None
        assert values["total"].evidence["row"] == 6
        assert values["member"].value is None
        assert values["member"].status == "empty"
        # The change rule fails: cash, total and change require confirmation; the merchant name is
        # fine.
        assert values["change"].needs_review
        assert values["cash"].needs_review
        assert not values["merchant"].needs_review


def test_error_rolls_back_everything(factory, report, receipt_file):
    with pytest.raises(RuntimeError), session_scope(factory) as session:
        save_document(session, receipt_file, [report])
        raise RuntimeError("保存到一半出错")

    with session_scope(factory) as session:
        assert session.scalars(select(Document)).all() == []
        assert session.scalars(select(Extraction)).all() == []
        assert session.scalars(select(ExtractedValue)).all() == []
