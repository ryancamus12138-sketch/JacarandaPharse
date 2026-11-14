"""Shared test fixtures. pytest loads this file automatically; its fixtures are available to all
tests."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from jacara.app import create_app
from jacara.core.config import get_settings
from jacara.core.db import Base, session_scope, sqlite_pragmas
from jacara.core.deps import get_ocr, get_queue, get_session, get_storage
from jacara.core.i18n import render
from jacara.core.queue import QueueError
from jacara.core.storage import StorageError
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.ocr.base import OcrLine, OcrPage, Point
from jacara.plugins.validators import RuleValidator


@pytest.fixture(autouse=True)
def personal_mode(monkeypatch, tmp_path):
    """Tests run in personal mode by default, regardless of the local .env. Tests for team mode
    switch explicitly. Settings-page values are redirected to a temporary folder as well, so the
    local data/settings.json is never read."""
    from jacara.core import config

    monkeypatch.setenv("JACARA_MODE", "personal")
    for name in ("JACARA_HOME", "JACARA_DATABASE", "JACARA_STORAGE", "JACARA_QUEUE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "page_settings_path", lambda: tmp_path / "page-settings.json")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@contextmanager
def raises(kind: type[BaseException], pattern: str) -> Iterator[Any]:
    """pytest.raises whose pattern is searched in the rendered message: messages that may be shown
    later are stored as template plus parameters and rendered in the reader's language."""
    with pytest.raises(kind) as info:
        yield info
    message = render(str(info.value))
    assert re.search(pattern, message), message


def make_line(text: str, x0: float, y0: float, x1: float, y1: float, conf: float = 0.99) -> OcrLine:
    """Build a fake OCR text block from text and a bounding box."""
    polygon = (Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1))
    return OcrLine(text=text, polygon=polygon, confidence=conf)


def make_page(*lines: OcrLine) -> OcrPage:
    return OcrPage(
        width=500, height=1000, lines=tuple(lines), engine="test", models={}, duration_ms=0
    )


@pytest.fixture
def receipt_page() -> OcrPage:
    """A reduced receipt based on the real OCR result of SROIE sample 0, including the OCR error
    (change read as 100)."""
    return make_page(
        make_line("BOOK TA _K (TAMAN DAYA) SDN BHD", 68, 91, 423, 115),
        make_line("NO.55 55,57 & 59, JALAN SAGU 18,", 108, 142, 383, 165),
        make_line("TAMAN DAYA,", 190, 166, 280, 186),
        make_line("Date:", 48, 369, 100, 390),
        make_line("25/12/2018 8:13:39 PM", 163, 369, 345, 390),
        make_line("1 PC", 71, 593, 113, 616),
        make_line("9.000", 201, 593, 250, 614),
        make_line("9.00", 408, 593, 447, 614),
        make_line("Total :", 241, 636, 300, 657),
        make_line("9.00", 407, 635, 446, 656),
        make_line("Cash", 202, 743, 240, 765),
        make_line("10.00", 394, 743, 446, 768),
        make_line("CHANGE", 203, 767, 260, 789),
        make_line("100", 407, 768, 447, 791),
        make_line("引物出门", 140, 881, 200, 900, conf=0.64),
    )


@pytest.fixture
def report(receipt_page) -> DocumentReport:
    """A result after the full pipeline: change read as 100, so the rule "cash − total = change"
    fails."""
    fields = (
        F("merchant", "string", "BOOK TA _K (TAMAN DAYA) SDN BHD", 1),
        F("total", "number", 9.0, 6),
        F("cash", "number", 10.0, 7),
        F("change", "number", 100.0, 8),
        F("member", "string", None, None),
    )
    result = ExtractionResult(fields, '{"raw": "模型原话"}', "test-model", "fake", "extract-v2", 5)
    return DocumentReport(
        source="receipt.jpg",
        extraction=result,
        aligned=TextMatchAligner().align(receipt_page, result),
        checks=RuleValidator().validate(fields),
        ocr_engine="rapidocr test",
        processed_at=datetime(2026, 11, 4),
    )


@pytest.fixture
def factory() -> sessionmaker[Session]:
    """In-memory SQLite database, fresh for every test and discarded afterwards.

        StaticPool: all sessions share one connection. Every new connection to in-memory SQLite
    is a new empty database, and the API and background processing open several sessions, so they
    must share one."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Same connection settings as the desktop edition (foreign keys enforced, as in PostgreSQL).
    event.listen(engine, "connect", sqlite_pragmas)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class MemoryStorage:
    """Fake object storage backed by a dictionary. Implements the ObjectStorage interface."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        if key not in self.files:
            raise StorageError(
                f"找不到文件：{key}"
            )  # Raises StorageError, like the real S3Storage.
        return self.files[key][0]

    def delete(self, key: str) -> None:
        self.files.pop(key, None)

    def keys(self) -> list[str]:
        return list(self.files)

    def content_type(self, key: str) -> str:
        return self.files[key][1]


class FakeQueue:
    """Fake task queue: records enqueued document ids without sending to Valkey. down=True simulates
    Valkey being unavailable."""

    def __init__(self) -> None:
        self.sent: list[uuid.UUID] = []
        self.evals: list[uuid.UUID] = []
        self.down = False

    def enqueue_document(self, document_id: uuid.UUID) -> None:
        if self.down:
            raise QueueError("无法连接任务队列（Valkey）")
        self.sent.append(document_id)

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        if self.down:
            raise QueueError("无法连接任务队列（Valkey）")
        self.evals.append(run_id)


class RegionOcr:
    """Fake OCR: recognizes the text in lines regardless of the image. Region re-recognition tests
    modify it."""

    name = "fake"

    def __init__(self) -> None:
        self.lines: list[str] = ["9.99"]
        self.seen: list[tuple[int, int]] = []
        """Image size of each recognition call, to verify cropping and upscaling."""

    def recognize(self, image):
        from PIL import Image

        with Image.open(image) as picture:
            self.seen.append(picture.size)
        lines = tuple(
            make_line(text, 0, i * 30, 100, i * 30 + 20, conf=0.9)
            for i, text in enumerate(self.lines)
        )
        return OcrPage(width=100, height=100, lines=lines, engine="fake", models={}, duration_ms=0)

    line: tuple[str, float] | None = None
    """Text recognized when the whole region is read as one line without detection (used only when
    detection finds nothing)."""

    def read_line(self, image):
        return self.line


region_ocr = RegionOcr()


@pytest.fixture
def storage() -> MemoryStorage:
    return MemoryStorage()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def client(factory, storage, queue) -> Iterator[TestClient]:
    """API client with database, object storage and task queue replaced by the fakes above."""
    app = create_app()

    def test_session() -> Iterator[Session]:
        with session_scope(factory) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_ocr] = lambda: region_ocr
    with TestClient(app) as test_client:
        yield test_client
