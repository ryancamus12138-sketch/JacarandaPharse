"""jacara start：密钥没改不让启动；Ctrl + C 时把后台进程都停掉。

不真的启动 API 和 worker：在启动之前就会被拦下来，或者用假的进程代替。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from jacara import cli
from jacara.core.config import Settings, get_settings

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


def test_start_refuses_port_used_by_another_program(secrets, capsys):
    import socket

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert cli.main(["start", "--port", str(port), "--no-browser"]) == 1
    assert "被别的程序占用" in capsys.readouterr().out


def test_stop_ends_every_child():
    children = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(2)
    ]
    cli._stop(children)
    assert all(child.poll() is not None for child in children)


class FakeProcess:
    """假的后台进程：一启动就算已经结束了。"""

    pid = 99999

    def __init__(self, *args, **kwargs) -> None:
        pass

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def started(secrets, monkeypatch, tmp_path):
    """不真的启动：后台进程、Web 服务、浏览器都换成假的，进程号文件写到临时文件夹。

    返回 Popen 收到的命令，看启动了哪些进程。
    """
    import uvicorn

    commands: list[list[str]] = []

    class Recording(FakeProcess):
        def __init__(self, command, *args, **kwargs) -> None:
            commands.append(command)

    monkeypatch.setattr(subprocess, "Popen", Recording)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "RUN_FILE", tmp_path / ".run" / "jacara.json")
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    monkeypatch.setattr(cli, "_wait_free", lambda port: True)
    monkeypatch.setattr(cli, "_infra_problems", lambda settings: [])
    # 模型密钥写到临时文件夹；环境变量测完恢复原样
    from jacara.core import config

    monkeypatch.setattr(config, "MODEL_KEY_FILE", tmp_path / ".run" / "model-key")
    monkeypatch.setenv("MODEL_API_KEY", "")
    return commands


def test_start_warns_when_there_is_no_model(started, capsys):
    """没有模型、8080 上也没有模型服务：照样启动，但要提醒"上传的文件会处理失败"。"""
    assert cli.main(["start", "--no-model", "--no-browser"]) == 0
    output = capsys.readouterr().out
    assert "没有模型服务" in output
    assert len(started) == 1  # 只有 worker


def test_start_is_quiet_when_a_model_is_already_running(started, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_port_free", lambda port: port != cli.MODEL_PORT)
    assert cli.main(["start", "--no-model", "--no-browser"]) == 0
    assert "没有模型服务" not in capsys.readouterr().out


def test_start_finds_the_model_by_itself(started, monkeypatch, tmp_path):
    """不写 --model：用 models/ 里唯一的 .gguf，一个命令就能把模型一起启动。"""
    from jacara.plugins.model_backends import server

    model = tmp_path / "only.gguf"
    model.write_bytes(b"")
    monkeypatch.setattr(Settings, "find_model", lambda self: model)
    monkeypatch.setattr(
        server, "server_command", lambda m, port, mmproj=None: ["llama-server", str(m)]
    )
    monkeypatch.setattr(server, "server_env", lambda: {})
    assert cli.main(["start", "--no-browser"]) == 0
    assert started[0] == ["llama-server", str(model)]
    # 每次启动生成密钥：只有自己能读的文件 + 传给 worker 的环境变量
    from jacara.core import config

    key = config.MODEL_KEY_FILE.read_text(encoding="utf-8")
    assert len(key) > 20 and config.MODEL_KEY_FILE.stat().st_mode & 0o077 == 0
    assert config.model_api_key() == key


def test_start_reuses_a_model_that_is_already_running(started, monkeypatch, tmp_path, capsys):
    model = tmp_path / "only.gguf"
    model.write_bytes(b"")
    monkeypatch.setattr(cli, "_port_free", lambda port: port != cli.MODEL_PORT)
    monkeypatch.setattr(cli, "_wait_free", lambda port: False)
    monkeypatch.setattr(cli, "_answers", lambda url, loading_ok=False: True)
    assert cli.main(["start", "--model", str(model), "--no-browser"]) == 0
    assert "已经有模型服务" in capsys.readouterr().out
    assert len(started) == 1  # 没有再启动一个模型


def test_second_start_just_opens_the_browser(secrets, monkeypatch, capsys):
    """已经在运行了：不报错，告诉用户地址，并打开浏览器。"""
    opened: list[str] = []
    monkeypatch.setattr(cli, "_port_free", lambda port: False)
    monkeypatch.setattr(cli, "_answers", lambda url, loading_ok=False: True)
    monkeypatch.setattr("webbrowser.open", opened.append)
    assert cli.main(["start"]) == 0
    assert "已经在运行了" in capsys.readouterr().out
    assert opened == ["http://127.0.0.1:8000"]


def test_start_removes_its_pid_file_when_it_stops(started):
    assert cli.main(["start", "--no-model", "--no-browser"]) == 0
    assert not cli.RUN_FILE.exists()


def test_stop_without_a_running_start(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "RUN_FILE", tmp_path / "jacara.json")
    assert cli.main(["stop"]) == 0
    assert "没有找到" in capsys.readouterr().out


def finished_pid() -> int:
    """一个已经结束的进程的进程号。"""
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def test_stop_asks_start_like_ctrl_c(monkeypatch, tmp_path, capsys):
    """jacara stop 给 start 发 SIGINT（和 Ctrl + C 一样），start 按正常流程收尾。"""
    import json
    import os
    import signal

    run_file = tmp_path / "jacara.json"
    run_file.write_text(json.dumps({"main": 12345, "children": []}))
    alive = {12345}
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        if pid not in alive:
            raise ProcessLookupError
        if sig != 0:
            sent.append((pid, sig))
            alive.discard(pid)  # 假装 start 收到信号后正常退出了

    monkeypatch.setattr(cli, "RUN_FILE", run_file)
    monkeypatch.setattr(os, "kill", fake_kill)
    assert cli.main(["stop"]) == 0
    assert sent == [(12345, signal.SIGINT)]
    assert not run_file.exists()
    assert "全部停止" in capsys.readouterr().out


def test_stop_cleans_up_children_left_behind(monkeypatch, tmp_path):
    """start 被强制结束了，模型和 worker 还在：jacara stop 把它们也停掉。"""
    import json

    run_file = tmp_path / "jacara.json"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    run_file.write_text(json.dumps({"main": finished_pid(), "children": [child.pid]}))
    monkeypatch.setattr(cli, "RUN_FILE", run_file)
    assert cli.main(["stop"]) == 0
    assert child.wait(timeout=10) is not None
    assert not run_file.exists()


def test_start_survives_sigterm_like_ctrl_c():
    """SIGTERM 会变成 KeyboardInterrupt，start 的 finally 才能把子进程停掉。"""
    with pytest.raises(KeyboardInterrupt):
        cli._interrupt(15, None)


def test_wait_free_gives_the_old_model_time_to_exit(monkeypatch):
    """刚按了 Ctrl + C，上一次的模型还占着端口：等它几秒，空出来就继续启动。"""
    answers = iter([False, False, True])
    monkeypatch.setattr(cli, "_port_free", lambda port: next(answers))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert cli._wait_free(8080) is True


def test_port_recently_closed_counts_as_free():
    """服务刚停、端口还在 TIME_WAIT：算空闲。真的有程序在监听：算占用。"""
    import socket

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _ = server.accept()
    assert cli._port_free(port) is False  # 正在监听
    accepted.close()  # 服务端先关：这一端的连接进入 TIME_WAIT
    client.close()
    server.close()
    assert cli._port_free(port) is True


def test_old_web_build_is_noticed(tmp_path):
    """改了网页代码却没重新打包：jacara start 提醒，不然浏览器里还是旧页面。"""
    import os

    from jacara.cli import _web_outdated

    dist, src = tmp_path / "dist", tmp_path / "src"
    dist.mkdir()
    (src / "features").mkdir(parents=True)
    page = src / "features" / "Page.tsx"
    page.write_text("x")
    index = dist / "index.html"
    index.write_text("x")
    os.utime(page, (1000, 1000))
    os.utime(index, (2000, 2000))
    assert not _web_outdated(dist)  # 打包在改代码之后
    os.utime(page, (3000, 3000))
    assert _web_outdated(dist)  # 代码比打包的新
    assert not _web_outdated(tmp_path / "nowhere")  # 没打包过：另有提醒


def test_start_refuses_when_docker_is_not_running(secrets, monkeypatch, capsys):
    """数据库、队列在 Docker 里：没开就直接说清楚怎么办，不要打开网页后才报错。"""
    from jacara.core.health import Check

    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    monkeypatch.setattr(
        "jacara.core.health.check_postgres",
        lambda s: Check("PostgreSQL", False, "Connection refused"),
    )
    monkeypatch.setattr("jacara.core.health.check_valkey", lambda s: Check("Valkey", True, "ok"))
    assert cli.main(["start", "--no-model", "--no-browser"]) == 1
    output = capsys.readouterr().out
    assert "PostgreSQL 连不上" in output and "docker compose up -d" in output


def test_start_asks_for_migration(secrets, monkeypatch, capsys):
    from jacara.core.health import Check

    ok = Check("x", True, "ok")
    monkeypatch.setattr(cli, "_port_free", lambda port: True)
    monkeypatch.setattr("jacara.core.health.check_postgres", lambda s: ok)
    monkeypatch.setattr("jacara.core.health.check_valkey", lambda s: ok)
    monkeypatch.setattr(cli, "_migration_behind", lambda: True)
    assert cli.main(["start", "--no-model", "--no-browser"]) == 1
    assert "alembic upgrade head" in capsys.readouterr().out
