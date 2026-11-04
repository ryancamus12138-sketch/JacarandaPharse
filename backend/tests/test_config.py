"""配置：默认值、从环境变量读取、从 .env 读取、密码不会被打印出来。"""

import pytest

from jacara.core.config import Settings


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
