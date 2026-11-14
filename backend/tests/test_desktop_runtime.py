"""Desktop runtime: SQLite database file, local file storage and the Huey queue, selected with
JACARA_DATABASE, JACARA_STORAGE and JACARA_QUEUE, all kept in the data directory (JACARA_HOME)."""

from __future__ import annotations

import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

import jacara.models  # noqa: F401  Every table must be known to the metadata comparison.
from jacara.core import config, queue
from jacara.core import storage as storage_module
from jacara.core.config import Settings, get_settings
from jacara.core.db import Base, create_db_engine
from jacara.core.storage import LocalStorage, StorageError
from jacara.models import Document, ExtractedValue, ValueRevision
from jacara.ops.backup import backup, restore
from jacara.ops.commands import migrate_to
from jacara.ops.verify import verify

from .test_backup import count, filled, png  # noqa: F401  filled is a fixture.


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A desktop data directory with all three switches set; caches that depend on them are
    cleared before and after."""
    monkeypatch.setenv("JACARA_HOME", str(tmp_path))
    monkeypatch.setenv("JACARA_DATABASE", "sqlite")
    monkeypatch.setenv("JACARA_STORAGE", "local")
    monkeypatch.setenv("JACARA_QUEUE", "huey")
    caches = (get_settings, storage_module.get_storage, queue.get_hueys, queue.huey_tasks)
    for cache in caches:
        cache.cache_clear()
    yield tmp_path
    for cache in caches:
        cache.cache_clear()


# ---- Settings ----


def test_data_home_defaults_to_project_root(monkeypatch):
    monkeypatch.delenv("JACARA_HOME", raising=False)
    assert config.data_home() == config.PROJECT_ROOT


def test_sqlite_url_and_secrets_follow_the_switches(home):
    settings = Settings()
    assert settings.database_url == f"sqlite:///{(home / 'jacara.db').as_posix()}"
    # No PostgreSQL password or S3 keys are needed, so none is reported as missing.
    assert settings.weak_secrets() == []
    server = Settings(
        _env_file=None,  # type: ignore[call-arg]  Not the local .env.
        jacara_database="postgres",
        jacara_storage="s3",
        postgres_password="",
        s3_access_key="",
        s3_secret_key="",
    )
    assert server.weak_secrets() == ["POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"]


def test_runtime_files_live_in_the_data_directory(home):
    (home / "models").mkdir()
    (home / "models" / "only.gguf").write_bytes(b"x")
    assert Settings().find_model() == home / "models" / "only.gguf"


# ---- SQLite ----


def test_sqlite_connections_use_wal_and_foreign_keys(home):
    engine = create_db_engine(Settings().database_url)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_migrations_build_the_full_schema_on_sqlite(home):
    """Every migration runs on an empty SQLite file, and the result matches the models exactly."""
    url = Settings().database_url
    migrate_to(url, None)
    engine = create_db_engine(url)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []
        # The comparison does not cover column defaults: every default written by the migrations
        # must also be a valid SQLite expression (PostgreSQL's now() is not).
        tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
        for (table,) in tables.all():
            for column in conn.execute(text(f'PRAGMA table_info("{table}")')).all():
                default = column[4]
                if default is not None:
                    conn.execute(text(f"SELECT {default}"))
    engine.dispose()


# ---- Local storage ----


def test_local_storage_round_trip(tmp_path):
    store = LocalStorage(tmp_path / "files")
    assert store.keys() == []
    store.put("documents/a/receipt.jpg", b"jpeg", "image/jpeg")
    store.put("documents/a/receipt.jpg", b"jpeg2", "image/jpeg")
    store.put("ocr-samples/x.png", b"png", "image/png")
    assert store.get("documents/a/receipt.jpg") == b"jpeg2"
    assert store.keys() == ["documents/a/receipt.jpg", "ocr-samples/x.png"]
    assert store.content_type("ocr-samples/x.png") == "image/png"
    assert store.content_type("ocr-samples/x.png.unknownext") == "application/octet-stream"
    # No temporary files are left behind or reported.
    assert sorted(p.name for p in (tmp_path / "files" / "documents" / "a").iterdir()) == [
        "receipt.jpg"
    ]
    store.delete("documents/a/receipt.jpg")
    store.delete("documents/a/receipt.jpg")  # A missing object is not an error.
    assert store.keys() == ["ocr-samples/x.png"]
    with pytest.raises(StorageError):
        store.get("documents/a/receipt.jpg")


@pytest.mark.parametrize(
    "key",
    ["", "../outside.txt", "a/../../outside.txt", "/etc/passwd", "a//b", "a\\b", "C:/x", "./a"],
)
def test_local_storage_rejects_keys_outside_its_folder(tmp_path, key):
    store = LocalStorage(tmp_path / "files")
    with pytest.raises(StorageError):
        store.put(key, b"x", "text/plain")
    assert not (tmp_path / "outside.txt").exists()


def test_storage_switch(home):
    chosen = storage_module.get_storage()
    assert isinstance(chosen, LocalStorage) and chosen.root == home / "files"


@pytest.mark.usefixtures("filled")
def test_backup_restores_into_a_desktop_data_directory(factory, storage, tmp_path, home):
    """A backup made on the server (here: the test database) restores into a new SQLite file and
    local folder, with every item opened afterwards."""
    archive = backup(factory.kw["bind"], storage, tmp_path, "test")
    url = Settings().database_url
    engine = create_db_engine(url)
    target = LocalStorage(home / "files")
    restore(archive, engine, target, lambda revision: migrate_to(url, revision))
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    for model in (Document, ExtractedValue, ValueRevision):
        assert count(maker, model) == count(factory, model)
    assert sorted(target.keys()) == sorted(storage.files)
    checks = verify(maker, target)
    assert all(c.passed for c in checks), [c.line() for c in checks]
    with maker() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 1
    engine.dispose()


# ---- Huey queue ----


def test_huey_queue_runs_the_worker_jobs(home, monkeypatch):
    from jacara import worker

    done: list[tuple[str, ...]] = []
    monkeypatch.setattr(worker, "process_document_job", lambda d: done.append(("process", d)))
    monkeypatch.setattr(worker, "extract_document_job", lambda d, a: done.append(("extract", d, a)))
    monkeypatch.setattr(worker, "eval_ocr_job", lambda r: done.append(("eval", r)))

    chosen = queue.create_queue()
    assert isinstance(chosen, queue.HueyQueue)
    document, run = uuid.uuid4(), uuid.uuid4()
    chosen.enqueue_document(document)
    chosen.enqueue_eval(run)
    chosen.enqueue_extract(document, "attempt-1")
    assert (home / "queue.db").is_file()

    hueys = queue.get_hueys()
    assert len(hueys[queue.QUEUE]) == 2 and len(hueys[queue.EXTRACT_QUEUE]) == 1
    for name in (queue.QUEUE, queue.EXTRACT_QUEUE):
        huey = hueys[name]
        while (task := huey.dequeue()) is not None:
            huey.execute(task)
    assert done == [
        ("process", str(document)),
        ("eval", str(run)),
        ("extract", str(document), "attempt-1"),
    ]


def test_server_queue_stays_celery(monkeypatch):
    monkeypatch.delenv("JACARA_QUEUE", raising=False)
    get_settings.cache_clear()
    assert isinstance(queue.create_queue(), queue.CeleryQueue)
