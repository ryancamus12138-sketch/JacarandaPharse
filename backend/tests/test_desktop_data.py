"""Desktop app data folder: full and automatic backups, restoring them (with the current data set
aside and put back on failure), moving the data folder, and holding off requests meanwhile."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import threading
import time
from collections import namedtuple
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jacara.core.config import get_settings
from jacara.core.maintenance import MAINTENANCE, Job, Kind
from jacara.core.runtime import reset_caches
from jacara.ops import desktop_data
from jacara.ops.commands import migrate_to
from jacara.ops.desktop_data import (
    DataError,
    auto_backup,
    check_move,
    full_backup,
    list_backups,
    models_missing,
    move_data,
    recorded_location,
    restore_backup,
)

from .test_backup import png


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A desktop data folder with a database, and the app (without the launcher) on top of it."""
    data = tmp_path / "data"
    data.mkdir()
    for name, value in {
        "JACARA_HOME": str(data),
        "JACARA_DEFAULT_HOME": str(data),
        "JACARA_DATABASE": "sqlite",
        "JACARA_STORAGE": "local",
        "JACARA_QUEUE": "huey",
        "JACARA_DESKTOP": "true",
    }.items():
        monkeypatch.setenv(name, value)
    reset_caches()
    migrate_to(get_settings().database_url, None)
    yield data
    reset_caches()
    MAINTENANCE.job = None
    MAINTENANCE.unavailable = None


@pytest.fixture
def app(home):
    from jacara.app import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        yield client


def upload(app, name: str = "receipt.png") -> str:
    response = app.post("/api/v1/documents", files={"file": (name, png(), "image/png")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def names(app) -> list[str]:
    return sorted(d["filename"] for d in app.get("/api/v1/documents").json()["items"])


def job(kind: Kind = "backup") -> Job:
    return Job(kind, "writes")


def test_full_backup_carries_settings_and_models_and_restores(app, home):
    # The desktop app runs the model in use in the model library.
    model = home / "models" / "hub" / "Model-Q4" / "v1" / "Model-Q4.gguf"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model weights")
    (home / "models" / "selected.json").write_text(
        json.dumps({"model": "models/hub/Model-Q4/v1/Model-Q4.gguf"}), "utf-8"
    )
    settings = home / "data" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"jacara_learn": False}), "utf-8")
    upload(app, "a.png")
    without = full_backup(job(), None, with_models=False)
    with_models = full_backup(job(), None, with_models=True)
    assert without["documents"] == 1 and not without["models"] and with_models["models"]
    kinds = {b.name: (b.kind, b.models) for b in list_backups()}
    assert kinds[Path(with_models["path"]).name] == ("full", True)

    upload(app, "b.png")
    model.unlink()
    settings.unlink()
    reset_caches()
    result = restore_backup(job("restore"), Path(with_models["path"]))
    reset_caches()
    assert names(app) == ["a.png"]
    assert model.read_bytes() == b"model weights"
    assert json.loads(settings.read_text("utf-8")) == {"jacara_learn": False}
    aside = Path(result["aside"])
    assert aside.parent == home and (aside / "jacara.db").is_file() and (aside / "files").is_dir()
    # A backup without model files lists what to download again.
    model.unlink()
    assert models_missing(Path(without["path"])) == ["hub/Model-Q4/v1/Model-Q4.gguf"]


def test_a_failed_restore_puts_the_data_back(app, home, monkeypatch):
    upload(app, "a.png")
    archive = Path(full_backup(job(), None, with_models=False)["path"])
    upload(app, "b.png")
    reset_caches()
    broken = namedtuple("Check", "name passed failed")
    monkeypatch.setattr(
        "jacara.ops.verify.verify",
        lambda *_a: [broken("原始文件（原图、PDF）", False, ["a.png: broken"])],
    )
    with pytest.raises(DataError, match="核对未通过"):
        restore_backup(job("restore"), archive)
    reset_caches()
    assert names(app) == ["a.png", "b.png"]
    assert not list(home.glob("before-restore-*"))


def test_a_backup_from_a_newer_version_is_refused_untouched(app, home, monkeypatch):
    upload(app)
    archive = Path(full_backup(job(), None, with_models=False)["path"])
    monkeypatch.setattr(desktop_data, "_known_revision", lambda _revision: False)
    with pytest.raises(DataError, match="更新的版本"):
        restore_backup(job("restore"), archive)
    assert not list(home.glob("before-restore-*")) and (home / "jacara.db").is_file()


def test_automatic_database_backups_keep_the_newest(app, home):
    upload(app, "a.png")
    made = []
    for _ in range(3):
        made.append(auto_backup(keep=2))
        time.sleep(0.05)  # Two backups within one second get distinct names.
    kept = sorted(p.name for p in (home / "backups" / "auto").glob("*.db"))
    assert kept == sorted(p.name for p in made[1:])
    assert {b.kind for b in list_backups()} == {"database"}


def test_restoring_a_database_backup_keeps_the_files_and_reports_missing_ones(app, home):
    upload(app, "a.png")
    database = auto_backup(keep=5)
    upload(app, "b.png")
    reset_caches()
    result = restore_backup(job("restore"), database)
    reset_caches()
    assert names(app) == ["a.png"]
    assert result["kind"] == "database" and result["warnings"] == []
    assert (home / "files").is_dir()  # A database backup leaves the files where they are.

    # Files removed after the backup are reported, not treated as a failed restore.
    shutil.rmtree(home / "files")
    reset_caches()
    result = restore_backup(job("restore"), database)
    reset_caches()
    assert names(app) == ["a.png"]
    assert result["warnings"]
    assert (home / "jacara.db").is_file() and len(list(home.glob("before-restore-*"))) == 2


def test_a_file_that_is_not_a_database_backup_is_refused(app, home, tmp_path):
    other = tmp_path / "jacara-other.db"
    with contextlib.closing(sqlite3.connect(other)) as conn:
        conn.execute("CREATE TABLE t (x)")
    with pytest.raises(DataError, match="不是 JacarandaParse"):
        restore_backup(job("restore"), other)
    assert not list(home.glob("before-restore-*"))


def test_moving_the_data_folder_and_back(app, home, tmp_path):
    upload(app, "a.png")
    (home / "models").mkdir()
    (home / "models" / "Model-Q4.gguf").write_bytes(b"weights")
    (home / "files" / "pipeline" / "work").mkdir(parents=True)  # An empty working folder.
    drive = tmp_path / "drive"
    drive.mkdir()
    reset_caches()
    result = move_data(job("move"), drive)
    reset_caches()
    target = drive / "JacarandaParse"
    assert result["path"] == str(target) and os.environ["JACARA_HOME"] == str(target)
    assert recorded_location(home) == target
    assert (target / "jacara.db").is_file() and (target / "models" / "Model-Q4.gguf").is_file()
    assert not (home / "jacara.db").exists() and not (home / "files").exists()
    assert (target / "files" / "pipeline" / "work").is_dir()  # The layout moves as it is.
    assert names(app) == ["a.png"]
    original = app.get(f"/api/v1/documents/{app.get('/api/v1/documents').json()['items'][0]['id']}")
    assert original.status_code == 200

    move_data(job("move"), None)  # Back to the app's own place.
    reset_caches()
    assert os.environ["JACARA_HOME"] == str(home) and recorded_location(home) is None
    assert (home / "jacara.db").is_file() and not target.exists()
    assert names(app) == ["a.png"]


def test_a_move_is_checked_before_it_starts(app, home, tmp_path, monkeypatch):
    with pytest.raises(DataError, match="已经在这个位置"):
        check_move(None)
    with pytest.raises(DataError, match="里面"):
        check_move(home)
    occupied = tmp_path / "occupied"
    (occupied / "JacarandaParse").mkdir(parents=True)
    (occupied / "JacarandaParse" / "something").write_text("x")
    with pytest.raises(DataError, match="已经有内容"):
        check_move(occupied)
    small = tmp_path / "small"
    small.mkdir()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(desktop_data.shutil, "disk_usage", lambda _p: usage(10, 10, 0))
    with pytest.raises(DataError, match="空间不足"):
        check_move(small)


def test_requests_wait_while_a_job_holds_them(app, home):
    release = threading.Event()

    def hold(_job: Job) -> None:
        release.wait(10)

    MAINTENANCE.start("backup", "writes", hold)
    try:
        assert app.get("/api/v1/documents").status_code == 200
        refused = app.post("/api/v1/documents", files={"file": ("r.png", png(), "image/png")})
        assert refused.status_code == 503 and refused.json()["maintenance"] is True
        assert app.get("/api/v1/desktop/job").json()["running"] is True
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while MAINTENANCE.running() and time.monotonic() < deadline:
        time.sleep(0.05)

    release.clear()
    MAINTENANCE.start("restore", "everything", hold)
    try:
        assert app.get("/api/v1/documents").status_code == 503
        assert app.get("/api/v1/desktop/job").status_code == 200
    finally:
        release.set()


def test_a_missing_data_folder_answers_only_the_location_endpoints(app, home, tmp_path):
    MAINTENANCE.unavailable = tmp_path / "drive" / "JacarandaParse"
    refused = app.get("/api/v1/documents")
    assert refused.status_code == 503
    assert refused.json()["unavailable"] == str(MAINTENANCE.unavailable)
    location = app.get("/api/v1/desktop/location").json()
    assert location == {"unavailable": str(MAINTENANCE.unavailable), "default": str(home)}


def test_the_desktop_endpoints_need_the_desktop_app(home, monkeypatch):
    from jacara.app import create_app

    monkeypatch.setenv("JACARA_DESKTOP", "false")
    get_settings.cache_clear()
    with TestClient(create_app(), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/desktop/data").status_code == 404
        assert client.get("/api/v1/desktop/location").status_code == 404


def test_data_endpoint_lists_the_folder_and_backups(app, home):
    upload(app)
    full_backup(job(), None, with_models=False)
    data = app.get("/api/v1/desktop/data").json()
    assert data["path"] == str(home) and data["default"] == str(home)
    assert [b["kind"] for b in data["backups"]] == ["full"] and data["size"] > 0


class FakeServices:
    """Stands in for the launcher's child processes (jacara.desktop.Services)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        if name in {"start", "stop", "pause_workers", "resume_workers", "restart"}:
            return lambda: self.calls.append(name)
        raise AttributeError(name)


@pytest.fixture
def services(monkeypatch):
    from jacara import desktop

    fake = FakeServices()
    monkeypatch.setattr(desktop, "SERVICES", fake)
    return fake


def finished(app) -> dict:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = app.get("/api/v1/desktop/job").json()
        if not job["running"]:
            return job
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


def test_backup_restore_and_move_through_the_api(app, home, tmp_path, services):
    upload(app, "a.png")
    usb = tmp_path / "usb"
    usb.mkdir()
    started = app.post("/api/v1/desktop/backups", json={"destination": str(usb), "models": False})
    assert started.status_code == 202, started.text
    done = finished(app)
    assert done["kind"] == "backup" and done["error"] is None
    archive = Path(done["result"]["path"])
    assert archive.parent == usb and services.calls == ["pause_workers", "resume_workers"]

    upload(app, "b.png")
    assert app.post("/api/v1/desktop/restore", json={"path": str(archive)}).status_code == 202
    done = finished(app)
    assert done["error"] is None and done["result"]["models_missing"] == []
    assert services.calls[-2:] == ["stop", "start"]
    assert names(app) == ["a.png"]

    check = app.post("/api/v1/desktop/move/check", json={"parent": str(usb)})
    assert check.json()["target"] == str(usb / "JacarandaParse")
    assert app.post("/api/v1/desktop/move", json={"parent": str(usb)}).status_code == 202
    done = finished(app)
    assert done["error"] is None and done["result"]["path"] == str(usb / "JacarandaParse")
    assert app.get("/api/v1/desktop/data").json()["path"] == str(usb / "JacarandaParse")
    assert names(app) == ["a.png"]

    assert app.post("/api/v1/desktop/restart").status_code == 202
    assert finished(app)["error"] is None and services.calls[-1] == "restart"


def test_the_api_refuses_bad_paths(app, home, tmp_path, services):
    missing = str(tmp_path / "missing")
    assert app.post("/api/v1/desktop/backups", json={"destination": missing}).status_code == 400
    assert app.post("/api/v1/desktop/backups", json={"destination": "relative"}).status_code == 400
    assert app.post("/api/v1/desktop/restore", json={"path": missing + ".tar"}).status_code == 400
    refused = app.post("/api/v1/desktop/move/check", json={"parent": None})
    assert refused.status_code == 400 and "已经在这个位置" in refused.json()["detail"]
    assert app.post("/api/v1/desktop/move", json={"parent": str(home)}).status_code == 400
    assert services.calls == []


def test_a_failed_job_reports_its_error(app, home, tmp_path, services):
    broken = tmp_path / "jacara-broken.tar"
    broken.write_bytes(b"not a backup")
    assert app.post("/api/v1/desktop/restore", json={"path": str(broken)}).status_code == 202
    done = finished(app)
    assert done["error"] and done["result"] is None
    assert services.calls == ["stop", "start"]  # The services run again after a failed restore.
    assert names(app) == []


def test_the_data_folder_found_again_or_given_up(app, home, tmp_path, monkeypatch):
    from jacara import desktop

    opened: list[str] = []
    monkeypatch.setattr(desktop, "open_data", lambda: opened.append(os.environ["JACARA_HOME"]))
    away = tmp_path / "usb" / "JacarandaParse"
    MAINTENANCE.unavailable = away
    still = app.post("/api/v1/desktop/location/retry")
    assert still.status_code == 409 and opened == []
    away.mkdir(parents=True)  # A folder without the data (another disk at the same place).
    assert app.post("/api/v1/desktop/location/retry").status_code == 409
    (away / "jacara.db").touch()
    found = app.post("/api/v1/desktop/location/retry").json()
    assert found["unavailable"] is None and opened == [str(away)]

    desktop_data.record_location(home, away)
    MAINTENANCE.unavailable = away
    given_up = app.post("/api/v1/desktop/location/default").json()
    assert given_up["unavailable"] is None and opened[-1] == str(home)
    assert recorded_location(home) is None
