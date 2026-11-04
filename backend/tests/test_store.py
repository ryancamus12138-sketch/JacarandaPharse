"""保存到数据库：用内存里的 SQLite 测试，不需要启动 PostgreSQL。

SQLite 是一个"装在文件（或内存）里的小数据库"，Python 自带。表结构由同一份模型生成，
所以能测出"存进去、读出来"的逻辑对不对。PostgreSQL 特有的地方（JSONB 等）由迁移和 doctor 负责。
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from jacara.core.db import Base, session_scope
from jacara.core.owner import PERSONAL_OWNER
from jacara.models import Document, ExtractedValue, Extraction
from jacara.pipeline.store import file_sha256, save_document
from jacara.plugins.aligners import TextMatchAligner
from jacara.plugins.exporters import DocumentReport
from jacara.plugins.extractors import ExtractedField as F
from jacara.plugins.extractors import ExtractionResult
from jacara.plugins.validators import RuleValidator


@pytest.fixture
def factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://")  # 内存数据库：每个测试一个全新的，测完就没了
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def report(receipt_page) -> DocumentReport:
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
        assert document.user_id == PERSONAL_OWNER.user_id  # 数据带归属

        [extraction] = document.extractions
        assert extraction.model == "test-model"  # 来源
        assert extraction.prompt_version == "extract-v2"
        assert extraction.raw_output == '{"raw": "模型原话"}'  # 原始回答
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
        # 找零规则不通过：现金、总额、找零都要人工确认；商家名没问题
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
