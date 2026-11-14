"""Advice worded for how the instance runs: inside the desktop app messages name actions in the
app (restart JacarandaParse, settings pages) instead of terminal commands; the app reads no .env
file and checks only the services it uses."""

from __future__ import annotations

import pytest

from jacara.core.config import Settings, get_settings
from jacara.core.health import run_checks
from jacara.core.i18n import render
from jacara.plugins.model_backends import ModelBackendError, ModelConfig
from jacara.plugins.model_backends.llama_server import LlamaServerBackend


@pytest.fixture
def desktop(monkeypatch):
    monkeypatch.setenv("JACARA_DESKTOP", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_current_user_says_whether_this_is_the_desktop_app(client, monkeypatch):
    assert client.get("/api/v1/auth/me").json()["desktop"] is False
    monkeypatch.setenv("JACARA_DESKTOP", "true")
    get_settings.cache_clear()
    try:
        assert client.get("/api/v1/auth/me").json()["desktop"] is True
    finally:
        get_settings.cache_clear()


def test_the_desktop_app_reads_no_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("JACARA_LEARN=false\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    try:
        assert get_settings().jacara_learn is False  # Run from source: .env is read.
        monkeypatch.setenv("JACARA_DESKTOP", "true")
        get_settings.cache_clear()
        assert get_settings().jacara_learn is True
        assert get_settings().jacara_desktop is True
    finally:
        get_settings.cache_clear()


def test_only_the_services_in_use_are_checked():
    desktop = Settings(
        jacara_database="sqlite",
        jacara_queue="huey",
        jacara_storage="local",
        jacara_desktop=True,
        model_server="http://127.0.0.1:9",
    )
    checks = run_checks(desktop)
    assert [c.name for c in checks][-1] == "llama-server"
    assert len(checks) == 2  # The web pages and the model server; no PostgreSQL, Valkey, S3, .env.
    hint = checks[-1].hint or ""
    assert "JacarandaParse" in hint and "jacara serve" not in hint

    source = Settings(model_server="http://127.0.0.1:9")
    assert len(run_checks(source)) == 6


def unreachable() -> str:
    backend = LlamaServerBackend(ModelConfig(base_url="http://127.0.0.1:9"))
    with pytest.raises(ModelBackendError) as raised:
        backend.chat([])
    return render(str(raised.value))


def test_model_errors_name_the_terminal_command_when_run_from_source():
    assert "uv run jacara serve" in unreachable()


def test_model_errors_name_the_app_in_the_desktop_app(desktop):
    text = unreachable()
    assert "重新启动 JacarandaParse" in text
    assert "jacara serve" not in text


def test_settings_fixed_by_the_desktop_app_are_not_offered(client, desktop, monkeypatch):
    monkeypatch.setenv("JACARA_API_DOCS", "false")  # As jacara desktop sets it.
    keys = [item["key"] for item in client.get("/api/v1/settings").json()["items"]]
    assert "jacara_api_docs" not in keys and "jacara_learn" in keys


def test_settings_from_source_list_the_api_docs_switch(client):
    keys = [item["key"] for item in client.get("/api/v1/settings").json()["items"]]
    assert "jacara_api_docs" in keys
