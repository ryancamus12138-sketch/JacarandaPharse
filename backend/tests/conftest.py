"""测试共用的小工具。pytest 会自动加载这个文件，里面的 fixture 所有测试都能用。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from jacara.app import create_app
from jacara.core.config import get_settings
from jacara.core.db import Base, session_scope
from jacara.core.deps import get_ocr, get_queue, get_session, get_storage
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
    """测试默认用个人模式，不管你自己的 .env 里写的是什么。要测团队模式的文件再自己切过去。
    设置页改的设置（ADR 0063）也换到临时文件夹，不读你自己的 data/settings.json。"""
    from jacara.core import config

    monkeypatch.setenv("JACARA_MODE", "personal")
    monkeypatch.setattr(config, "page_settings_path", lambda: tmp_path / "page-settings.json")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def make_line(text: str, x0: float, y0: float, x1: float, y1: float, conf: float = 0.99) -> OcrLine:
    """造一块假的 OCR 文字：只要给出文字和外框。"""
    polygon = (Point(x0, y0), Point(x1, y0), Point(x1, y1), Point(x0, y1))
    return OcrLine(text=text, polygon=polygon, confidence=conf)


def make_page(*lines: OcrLine) -> OcrPage:
    return OcrPage(
        width=500, height=1000, lines=tuple(lines), engine="test", models={}, duration_ms=0
    )


@pytest.fixture
def receipt_page() -> OcrPage:
    """一张缩小版的收据：照着 SROIE 第 0 张的真实 OCR 结果写的，包括 OCR 的错（找零读成 100）。"""
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
    """一份走完流水线的结果：找零读成 100，规则"现金 − 总额 = 找零"不通过。"""
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
    """内存里的 SQLite 数据库，每个测试一个全新的，测完就没了。

    StaticPool：所有会话共用同一个连接。内存 SQLite 每开一个新连接就是一个新的空数据库，
    而 API 和后台处理都会开多个会话，必须共用同一个。
    """
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class MemoryStorage:
    """假的对象存储：文件存在一个字典里。满足 ObjectStorage 接口。"""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = (data, content_type)

    def get(self, key: str) -> bytes:
        if key not in self.files:
            raise StorageError(f"找不到文件：{key}")  # 和真的 S3Storage 一样，报 StorageError
        return self.files[key][0]

    def delete(self, key: str) -> None:
        self.files.pop(key, None)

    def keys(self) -> list[str]:
        return list(self.files)

    def content_type(self, key: str) -> str:
        return self.files[key][1]


class FakeQueue:
    """假的任务队列：只记下放进来的文档 id，不真的发给 Valkey。down=True 模拟 Valkey 没启动。"""

    def __init__(self) -> None:
        self.sent: list[uuid.UUID] = []
        self.evals: list[uuid.UUID] = []
        self.down = False

    def enqueue_document(self, document_id: uuid.UUID) -> None:
        if self.down:
            raise QueueError("任务队列（Valkey）连不上")
        self.sent.append(document_id)

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        if self.down:
            raise QueueError("任务队列（Valkey）连不上")
        self.evals.append(run_id)


class RegionOcr:
    """假的 OCR：不管给什么图，都认出 lines 里的文字。"重新识别这块"的测试改它。"""

    name = "fake"

    def __init__(self) -> None:
        self.lines: list[str] = ["9.99"]
        self.seen: list[tuple[int, int]] = []
        """每次识别的图片大小，看裁得对不对、放大了没有。"""

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
    """不检测、整块当一行直接识别时认出的文字（检测一个字都没找到时才用）。"""

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
    """调用 API 用的客户端：数据库、对象存储、任务队列都换成上面的假东西。"""
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
