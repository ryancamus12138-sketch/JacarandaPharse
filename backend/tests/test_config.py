"""配置：默认值、从环境变量读取、从 .env 读取、密码不会被打印出来。"""

import pytest

from jacara.core.config import Settings

FAKE_KEY = "not-a-real-key"
"""测试用的假密钥。故意写得一看就是假的，免得 gitleaks 当成真密钥拦下来。"""


@pytest.fixture(autouse=True)
def no_real_env_file(tmp_path, monkeypatch):
    """每个测试都在一个空文件夹里运行，读不到项目里真正的 .env。"""
    monkeypatch.chdir(tmp_path)


def test_defaults():
    settings = Settings()
    assert settings.postgres_host == "127.0.0.1"
    assert settings.valkey_url == "redis://127.0.0.1:6379/0"


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    monkeypatch.setenv("POSTGRES_PORT", "15432")
    settings = Settings()
    assert settings.postgres_port == 15432  # 文字 "15432" 自动转成整数
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
    """models/ 里只有一个 .gguf 就用它；没有或有好几个都不猜。"""
    from jacara.core import config

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    assert Settings().find_model() is None
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "a.gguf").write_bytes(b"")
    assert Settings().find_model() == tmp_path / "models" / "a.gguf"
    (tmp_path / "models" / "b.gguf").write_bytes(b"")
    assert Settings().find_model() is None
