"""jacara start：密钥没改不让启动；Ctrl + C 时把后台进程都停掉。

不真的启动 API 和 worker：在启动之前就会被拦下来，或者用假的进程代替。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from jacara import cli
from jacara.core.config import get_settings

FAKE_KEY = "not-a-real-key"
"""测试用的假密钥。故意写得一看就是假的，免得 gitleaks 当成真密钥拦下来。"""


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    """在空文件夹里运行（读不到你自己的 .env），密钥先都设成"不是 change-me"的值。"""
    monkeypatch.chdir(tmp_path)
    for name in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.setenv(name, FAKE_KEY)
    get_settings.cache_clear()


def test_start_refuses_placeholder_secrets(secrets, monkeypatch, capsys):
    monkeypatch.setenv("S3_SECRET_KEY", "change-me")
    get_settings.cache_clear()
    assert cli.main(["start"]) == 1
    output = capsys.readouterr().out
    assert "S3_SECRET_KEY" in output
    assert FAKE_KEY not in output  # 只说名字，不把密钥打印出来


def test_start_refuses_missing_model(secrets, tmp_path, capsys):
    assert cli.main(["start", "--model", str(tmp_path / "nope.gguf")]) == 1
    assert "找不到模型文件" in capsys.readouterr().out


def test_start_refuses_busy_port(secrets, capsys):
    import socket

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert cli.main(["start", "--port", str(port)]) == 1
    assert "已经被占用" in capsys.readouterr().out


def test_stop_ends_every_child():
    children = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(2)
    ]
    cli._stop(children)
    assert all(child.poll() is not None for child in children)
