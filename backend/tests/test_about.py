"""Settings → About and diagnostics: attribution and licenses, folders and background processes of
the desktop app, and the diagnostic text for problem reports."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from jacara import desktop as desktop_module
from jacara.core import config
from jacara.core.config import get_settings


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("JACARA_DESKTOP", "true")
    monkeypatch.setenv("JACARA_LOG_DIR", str(tmp_path / "Logs"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def program(monkeypatch, tmp_path):
    """The files shipped with the program."""
    folder = tmp_path / "program"
    folder.mkdir()
    (folder / "LICENSE").write_text("Apache License\nVersion 2.0", "utf-8")
    (folder / "NOTICE").write_text("JacarandaParse\nCopyright 2026 Ryan Camus\n", "utf-8")
    monkeypatch.setattr(config, "resource_dir", lambda: folder)
    return folder


def test_attribution_and_licenses_come_from_the_files_shipped(client, program):
    info = client.get("/api/v1/system").json()
    assert info["notice"] == "JacarandaParse\nCopyright 2026 Ryan Camus"
    assert info["license"] == "Apache-2.0" and info["third_party"] is False
    assert info["desktop"] is None  # Not the desktop app.
    assert client.get("/api/v1/system/licenses/license").json()["text"].startswith("Apache")
    missing = client.get("/api/v1/system/licenses/third_party")
    assert missing.status_code == 404 and "THIRD_PARTY_LICENSES" in missing.json()["detail"]
    (program / "THIRD_PARTY_LICENSES").write_text("numpy BSD-3-Clause", "utf-8")
    assert client.get("/api/v1/system").json()["third_party"] is True
    assert client.get("/api/v1/system/licenses/third_party").json()["text"] == "numpy BSD-3-Clause"
    assert client.get("/api/v1/system/licenses/other").status_code == 422


def test_the_desktop_app_shows_its_folders_and_processes(client, desktop, program, monkeypatch):
    services = desktop_module.Services(1, "key")
    child = desktop_module.Child("watch", ["true"], {})
    child.starts, child.given_up, child.last_note = 3, True, "exited 4 times right after starting"
    services.supervisor = desktop_module.Supervisor([child], lambda _c: None)
    monkeypatch.setattr(desktop_module, "SERVICES", services)
    info = client.get("/api/v1/system").json()["desktop"]
    assert info["log_dir"].endswith("Logs")
    assert info["data_dir"] == str(config.data_home())
    assert info["processes"] == [
        {
            "name": "watch",
            "state": "stopped",
            "starts": 3,
            "note": "exited 4 times right after starting",
        }
    ]
    opened: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda command, **_k: opened.append(command))
    assert client.post("/api/v1/desktop/open-folder?which=logs").status_code == 204
    assert opened and str(Path(info["log_dir"])) in " ".join(opened[0])


def test_the_diagnostic_text_has_no_home_folder_and_no_document_names(
    client, desktop, program, monkeypatch
):
    from jacara.plugins.model_backends import server as model_server

    monkeypatch.setattr(model_server, "llama_server_path", lambda _s: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: config.data_home().parent))
    image = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(image, "PNG")
    response = client.post(
        "/api/v1/documents", files={"file": ("Invoice of Alice.png", image.getvalue(), "image/png")}
    )
    assert response.status_code == 201, response.text
    text = client.get("/api/v1/system/diagnostics").json()["text"]
    assert text.startswith("JacarandaParse ")
    for part in ("[System]", "[Models]", "[Background processes]", "[Checks]", "[Data]"):
        assert part in text
    assert "Documents: 1" in text and "llama-server not found" in text
    assert "Invoice of Alice" not in text
    assert str(config.data_home().parent) not in text and "~/" in text
