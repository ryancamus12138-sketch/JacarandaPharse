"""Desktop app: downloads are saved to the Downloads folder by the service (the system web view
does not save them), saved files can be shown in Finder or File Explorer, and links to other sites
open in the default browser."""

from __future__ import annotations

import subprocess
import webbrowser

import pytest

from jacara.core import desktop_files
from jacara.core.config import get_settings
from jacara.core.desktop_files import filename_of, safe_name, unique_path


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    folder = tmp_path / "Downloads"
    monkeypatch.setattr(desktop_files, "downloads_folder", lambda: folder)
    return folder


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setenv("JACARA_DESKTOP", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


EXPORT = "/api/v1/templates/receipt/export?format=xlsx"
SAVE = {"X-Jacara-Save": "downloads"}


def test_file_names_are_made_safe_for_every_system():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("a:b*c?.xlsx") == "a_b_c_.xlsx"
    assert safe_name("CON.txt") == "_CON.txt"
    assert safe_name("report. ") == "report"
    assert safe_name("") == "download"


def test_an_existing_name_gets_a_number_as_browsers_do(tmp_path):
    assert unique_path(tmp_path, "a.xlsx").name == "a.xlsx"
    (tmp_path / "a.xlsx").write_bytes(b"x")
    (tmp_path / "a (1).xlsx").write_bytes(b"x")
    assert unique_path(tmp_path, "a.xlsx").name == "a (2).xlsx"
    (tmp_path / "notes").write_bytes(b"x")
    assert unique_path(tmp_path, "notes").name == "notes (1)"


def test_file_name_from_content_disposition():
    header = "attachment; filename=\"export.xlsx\"; filename*=UTF-8''%E6%94%B6%E6%8D%AE.xlsx"
    assert filename_of(header) == "收据.xlsx"
    assert filename_of('attachment; filename="plain.csv"') == "plain.csv"


def test_desktop_downloads_are_saved_to_the_downloads_folder(desktop, downloads, client):
    first = client.get(EXPORT, headers=SAVE)
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["path"].startswith(str(downloads)) and saved["name"].endswith(".xlsx")
    assert (downloads / saved["name"]).read_bytes()[:2] == b"PK"  # A complete xlsx (zip) file.
    second = client.get(EXPORT, headers=SAVE).json()
    assert second["name"] == saved["name"].removesuffix(".xlsx") + " (1).xlsx"
    assert not [p for p in downloads.iterdir() if p.name.endswith(".jacara-download")]
    # Without the header the file is returned as before.
    plain = client.get(EXPORT)
    assert plain.headers["content-disposition"].startswith("attachment")


def test_errors_are_returned_unchanged(desktop, downloads, client):
    missing = client.get("/api/v1/templates/no-such-template/export?format=xlsx", headers=SAVE)
    assert missing.status_code == 404
    assert not downloads.exists() or not list(downloads.iterdir())


def test_outside_the_desktop_app_nothing_is_saved(downloads, client):
    response = client.get(EXPORT, headers=SAVE)
    assert response.headers["content-disposition"].startswith("attachment")
    assert not downloads.exists()
    assert client.post("/api/v1/desktop/open-url", json={"url": "https://a.b"}).status_code == 404


def test_files_made_in_the_page_are_saved_too(desktop, downloads, client):
    response = client.post(
        "/api/v1/desktop/downloads",
        files={"file": ("x", b"a,b\n", "text/csv")},
        data={"filename": "审计日志.csv"},
    )
    assert response.status_code == 200, response.text
    assert (downloads / "审计日志.csv").read_bytes() == b"a,b\n"


def test_only_saved_downloads_can_be_shown(desktop, downloads, client, monkeypatch, tmp_path):
    shown: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **_kw: shown.append(args))
    saved = client.get(EXPORT, headers=SAVE).json()
    assert client.post("/api/v1/desktop/reveal", json=saved).status_code == 204
    assert shown and str(downloads) in " ".join(shown[0])
    elsewhere = tmp_path / "secret.txt"
    elsewhere.write_text("x")
    assert client.post("/api/v1/desktop/reveal", json={"path": str(elsewhere)}).status_code == 400
    assert len(shown) == 1


def test_only_web_addresses_open_in_the_browser(desktop, client, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    assert (
        client.post("/api/v1/desktop/open-url", json={"url": "https://example.org/a"}).status_code
        == 204
    )
    for bad in ("file:///etc/passwd", "javascript:alert(1)", "https://"):
        assert client.post("/api/v1/desktop/open-url", json={"url": bad}).status_code == 400
    assert opened == ["https://example.org/a"]


def test_system_dialogs_answer_the_chosen_path_or_none(desktop, client, monkeypatch, tmp_path):
    def answer(stdout: bytes, code: int = 0):
        def run(command, **_kwargs):
            return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=b"")

        monkeypatch.setattr(subprocess, "run", run)

    answer(f"{tmp_path}\n".encode())
    chosen = client.post("/api/v1/desktop/choose-folder", json={"purpose": "watch"})
    assert chosen.json() == {"path": str(tmp_path)}
    answer(b"", 1)  # Cancelled.
    assert client.post("/api/v1/desktop/choose-file").json() == {"path": None}


def test_a_system_without_the_dialog_program_asks_for_a_typed_path(desktop, client, monkeypatch):
    def missing(command, **_kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", missing)
    refused = client.post("/api/v1/desktop/choose-folder", json={"purpose": "move"})
    assert refused.status_code == 400 and "完整路径" in refused.json()["detail"]
    assert client.post("/api/v1/desktop/choose-file").status_code == 400
