"""Configuration: defaults, environment variables, .env, and passwords never printed."""

import pytest

from jacara.core.config import Settings

FAKE_KEY = "not-a-real-key"
"""Fake secret for tests, deliberately obvious so that gitleaks does not flag it as real."""


@pytest.fixture(autouse=True)
def no_real_env_file(tmp_path, monkeypatch):
    """Each test runs in an empty folder and cannot read the project's real .env."""
    monkeypatch.chdir(tmp_path)


def test_defaults():
    settings = Settings()
    assert settings.postgres_host == "127.0.0.1"
    assert settings.valkey_url == "redis://127.0.0.1:6379/0"


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_PORT", "15432")
    settings = Settings()
    assert settings.postgres_port == 15432  # The string "15432" is converted to an integer.
    assert settings.database_url == "postgresql+psycopg://jacara:s3cret@127.0.0.1:15432/jacara"


def test_env_file_is_read(tmp_path):
    (tmp_path / ".env").write_text("S3_BUCKET=receipts\nPOSTGRES_PASSWORD=from-file\n")
    settings = Settings()
    assert settings.s3_bucket == "receipts"
    assert settings.postgres_password.get_secret_value() == "from-file"


def test_secrets_are_hidden_when_printed(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    settings = Settings()
    assert "s3cret" not in repr(settings)
    assert "s3cret" not in str(settings.postgres_password)


def test_placeholder_secrets_are_found(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "change-me")
    monkeypatch.setenv("S3_ACCESS_KEY", "")
    monkeypatch.setenv("S3_SECRET_KEY", FAKE_KEY)
    assert Settings().weak_secrets() == ["POSTGRES_PASSWORD", "S3_ACCESS_KEY"]


def test_web_dist_defaults_to_project_web_folder(tmp_path, monkeypatch):
    assert Settings().web_dist.parts[-2:] == ("web", "dist")
    monkeypatch.setenv("JACARA_WEB_DIR", str(tmp_path / "site"))
    assert Settings().web_dist == tmp_path / "site"


def test_model_from_env_is_relative_to_the_project(monkeypatch):
    from jacara.core.config import PROJECT_ROOT

    monkeypatch.setenv("JACARA_MODEL", "models/a.gguf")
    assert Settings().find_model() == PROJECT_ROOT / "models" / "a.gguf"


def test_model_is_found_only_when_there_is_exactly_one(tmp_path, monkeypatch):
    """A single .gguf in models/ is used; with none or several, nothing is guessed."""
    from jacara.core import config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    assert Settings().find_model() is None
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.gguf").write_bytes(b"")
    assert Settings().find_model() == tmp_path / "models" / "a.gguf"
    (tmp_path / "models" / "b.gguf").write_bytes(b"")
    assert Settings().find_model() is None
