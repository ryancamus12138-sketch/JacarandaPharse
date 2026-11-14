"""Backup and restore: a complete backup, restored into an empty environment, with every item
opened."""

from __future__ import annotations

import io
import json
import tarfile
import uuid

import pytest
from PIL import Image
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jacara.core.db import Base, session_scope
from jacara.models import Document, ExtractedValue, OcrSample, ValueRevision
from jacara.ops.backup import BackupError, backup, check_archive, decode, encode, restore
from jacara.ops.verify import verify

from .conftest import MemoryStorage, raises
from .test_template_library import process


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(out, format="PNG")
    return out.getvalue()


def empty():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False), MemoryStorage()


@pytest.fixture
def filled(client, factory, storage, receipt_page):
    """Process a receipt, edit a field, collect a sample, set an avatar and store an OCR
    snapshot."""
    response = client.post("/api/v1/documents", files={"file": ("r.png", png(), "image/png")})
    document_id = response.json()["id"]
    process(factory, storage, document_id, receipt_page)
    detail = client.get(f"/api/v1/documents/{document_id}").json()
    value = detail["extractions"][0]["values"][0]
    edit = client.post(
        f"/api/v1/values/{value['id']}/revisions",
        json={"action": "edit", "value": "改过的", "reason": "测试"},
    )
    assert edit.status_code == 201, edit.text
    with session_scope(factory) as session:
        session.add(
            OcrSample(
                document_id=uuid.UUID(document_id),
                page=1,
                unit_page=1,
                kind="line",
                polygon=[[0, 0], [1, 0], [1, 1], [0, 1]],
                transcription="9.00",
                ocr_text="9.00",
                category="correct",
                error_tags=[],
                template="收据",
                field="total",
                field_label="总额",
                source_format="image",
                ocr_engine="test",
                crop_key="ocr-samples/x.png",
                status="ready",
                error="",
                user_id=uuid.uuid4(),
                team_id=uuid.uuid4(),
            )
        )
    storage.put("ocr-samples/x.png", png(), "image/png")
    return document_id


def count(maker, model) -> int:
    with maker() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_values_round_trip():
    for value in (uuid.uuid4(), {"a": [1, {"$uuid": "x"}]}, b"\x00\xff", 1.5, None, "文字"):
        assert decode(json.loads(json.dumps(encode(value)))) == value


def test_backup_restore_and_open_everything(factory, storage, filled, tmp_path):
    archive = backup(factory.kw["bind"], storage, tmp_path, "test")
    manifest = check_archive(archive)
    assert manifest.tables["documents"] == 1
    assert any(k.startswith("ocr-snapshots/") for k in manifest.objects)
    assert "ocr-samples/x.png" in manifest.objects

    engine, maker, target = empty()
    restore(archive, engine, target, lambda revision: None)
    for model in (Document, ExtractedValue, ValueRevision, OcrSample):
        assert count(maker, model) == count(factory, model)
    assert target.files.keys() == storage.files.keys()
    assert all(target.files[k] == storage.files[k] for k in storage.files)

    checks = verify(maker, target)
    assert all(c.passed for c in checks), [c.line() for c in checks]
    names = {c.name: c.ok for c in checks}
    assert names["原始文件（原图、PDF）"] == 1 and names["OCR 样本小图"] == 1
    assert names["OCR 快照"] == 1


def test_damaged_archive_is_refused_before_anything_changes(factory, storage, filled, tmp_path):
    archive = backup(factory.kw["bind"], storage, tmp_path, "test")
    damaged = tmp_path / "damaged.tar"
    with tarfile.open(archive) as src, tarfile.open(damaged, "w") as dst:
        for member in src.getmembers():
            data = src.extractfile(member).read()  # type: ignore[union-attr]
            if member.name == "objects/ocr-samples/x.png":
                data = b"not the same"
            member.size = len(data)
            dst.addfile(member, io.BytesIO(data))
    engine, maker, target = empty()
    with raises(BackupError, "内容和备份时不一样"):
        restore(damaged, engine, target, lambda revision: None)
    assert count(maker, Document) == 0 and not target.files


def test_restore_only_into_an_empty_environment(factory, storage, filled, tmp_path):
    archive = backup(factory.kw["bind"], storage, tmp_path, "test")
    with raises(BackupError, "只能恢复到空的环境"):
        restore(archive, factory.kw["bind"], storage, lambda revision: None)
    engine, _, target = empty()
    target.put("something", b"x", "text/plain")
    with raises(BackupError, "对象存储里已经有文件"):
        restore(archive, engine, target, lambda revision: None)


def test_verify_reports_what_cannot_be_opened(factory, storage, filled):
    storage.files["ocr-samples/x.png"] = (b"broken", "image/png")
    checks = {c.name: c for c in verify(factory, storage)}
    assert not checks["OCR 样本小图"].passed
    assert checks["原始文件（原图、PDF）"].passed


def test_backup_refused_while_running(factory, storage, tmp_path, capsys):
    from jacara.ops.commands import run_backup

    assert run_backup(factory.kw["bind"], storage, tmp_path, "test", running=True) == 1  # type: ignore[arg-type]
    assert "jacara stop" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())
