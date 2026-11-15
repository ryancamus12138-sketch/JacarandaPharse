"""jacara start: refuses to start with unchanged secrets; stops all background processes on Ctrl +
C.

API and worker are never actually started: either startup is refused first, or fake processes are
used."""

from __future__ import annotations

import subprocess
import sys

import pytest

from jacara import cli
from jacara.core.config import Settings, get_settings

FAKE_KEY = "not-a-real-key"
"""Fake secret for tests, deliberately obvious so that gitleaks does not flag it as real."""


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    """Run in an empty folder (the local .env is not read), with all secrets set to values other
    than change-me."""
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
    assert FAKE_KEY not in output  # Only the name is printed, never the secret.


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
    """Fake background process that has already exited when started."""

    pid = 99999

    def __init__(self, *args, **kwargs) -> None:
        pass

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


@pytest.fixture
def started(secrets, monkeypatch, tmp_path):
    """Nothing is actually started: background processes, the web service and the browser are fakes,
    and the pid file is written to a temporary folder.

        Returns the commands passed to Popen, to check which processes were started."""
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
    monkeypatch.setattr(cli, "_reset_interrupted", lambda: None)  # No database connection.
    # The model key is written to a temporary folder; environment variables are restored afterwards.
    from jacara.core import config

    monkeypatch.setattr(config, "MODEL_KEY_FILE", tmp_path / ".run" / "model-key")
    monkeypatch.setenv("MODEL_API_KEY", "")
    return commands


def test_start_warns_when_there_is_no_model(started, capsys):
    """No model and no model server on port 8080: start anyway, warning that uploads will fail to
    process."""
    assert cli.main(["start", "--no-model", "--no-browser"]) == 0
    output = capsys.readouterr().out
    assert "没有模型服务" in output
    # Background tasks only: the OCR stage and the model stage.
    assert [c[-2:] for c in started] == [["--stage", "read"], ["--stage", "extract"]]


def test_start_one_worker_when_the_pipeline_is_off(started, monkeypatch):
    monkeypatch.setenv("JACARA_PIPELINE", "false")
    from jacara.core.config import get_settings

    get_settings.cache_clear()
    assert cli.main(["start", "--no-model", "--no-browser"]) == 0
    assert [c[-1] for c in started] == ["worker"]  # One process end to end.


def test_start_is_quiet_when_a_model_is_already_running(started, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_port_free", lambda port: port != cli.MODEL_PORT)
    assert cli.main(["start", "--no-model", "--no-browser"]) == 0
    assert "没有模型服务" not in capsys.readouterr().out


def test_start_finds_the_model_by_itself(started, monkeypatch, tmp_path):
    """Without --model: the only .gguf in models/ is used, so one command starts the model too."""
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
    # A key is generated on every start: an owner-readable file plus an environment variable passed
    # to the worker.
    from jacara.core import config

    key = config.MODEL_KEY_FILE.read_text(encoding="utf-8")
    assert len(key) > 20
    if sys.platform != "win32":  # No POSIX permission bits on Windows (see test_setup.py).
        assert config.MODEL_KEY_FILE.stat().st_mode & 0o077 == 0
    assert config.model_api_key() == key


def test_start_reuses_a_model_that_is_already_running(started, monkeypatch, tmp_path, capsys):
    model = tmp_path / "only.gguf"
    model.write_bytes(b"")
    monkeypatch.setattr(cli, "_port_free", lambda port: port != cli.MODEL_PORT)
    monkeypatch.setattr(cli, "_wait_free", lambda port: False)
    monkeypatch.setattr(cli, "_answers", lambda url, loading_ok=False: True)
    assert cli.main(["start", "--model", str(model), "--no-browser"]) == 0
    assert "已经有模型服务" in capsys.readouterr().out
    assert all("worker" in c for c in started)  # No second model started.


def test_second_start_just_opens_the_browser(secrets, monkeypatch, capsys):
    """Already running: no error; print the address and open the browser."""
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
    """Pid of a process that has already exited."""
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def test_stop_asks_start_like_ctrl_c(monkeypatch, tmp_path, capsys):
    """jacara stop sends SIGINT to start (like Ctrl + C), and start shuts down normally."""
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
            alive.discard(pid)  # Simulate start exiting normally after the signal.

    monkeypatch.setattr(cli, "RUN_FILE", run_file)
    monkeypatch.setattr(os, "kill", fake_kill)
    # On Windows _alive() asks the system instead of sending signal 0; the fake process is
    # known only here.
    monkeypatch.setattr(cli, "_alive", lambda pid: pid in alive)
    assert cli.main(["stop"]) == 0
    assert sent == [(12345, signal.SIGINT)]
    assert not run_file.exists()
    assert "全部停止" in capsys.readouterr().out


def test_stop_cleans_up_children_left_behind(monkeypatch, tmp_path):
    """start was killed while the model and worker keep running: jacara stop stops them too."""
    import json

    run_file = tmp_path / "jacara.json"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    run_file.write_text(json.dumps({"main": finished_pid(), "children": [child.pid]}))
    monkeypatch.setattr(cli, "RUN_FILE", run_file)
    assert cli.main(["stop"]) == 0
    assert child.wait(timeout=10) is not None
    assert not run_file.exists()


def test_start_survives_sigterm_like_ctrl_c():
    """SIGTERM becomes KeyboardInterrupt so that start's finally block stops the child processes."""
    with pytest.raises(KeyboardInterrupt):
        cli._interrupt(15, None)


def test_wait_free_gives_the_old_model_time_to_exit(monkeypatch):
    """Just after Ctrl + C the previous model still holds the port: wait a few seconds and continue
    once it is free."""
    answers = iter([False, False, True])
    monkeypatch.setattr(cli, "_port_free", lambda port: next(answers))
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert cli._wait_free(8080) is True


def test_port_recently_closed_counts_as_free():
    """A just-stopped service leaves the port in TIME_WAIT: free. A program actually listening: in
    use."""
    import socket

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    accepted, _ = server.accept()
    assert cli._port_free(port) is False  # Listening.
    accepted.close()  # The server closes first: this end of the connection enters TIME_WAIT.
    client.close()
    server.close()
    assert cli._port_free(port) is True


def test_old_web_build_is_noticed(tmp_path):
    """Frontend code changed without rebuilding: jacara start warns, since the browser would show
    the old page."""
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
    assert not _web_outdated(dist)  # Build after the code change.
    os.utime(page, (3000, 3000))
    assert _web_outdated(dist)  # Code newer than the build.
    assert not _web_outdated(tmp_path / "nowhere")  # Never built: a separate warning.


def test_start_refuses_when_docker_is_not_running(secrets, monkeypatch, capsys):
    """Database and queue run in Docker: when it is not running, say how to fix it instead of
    failing after the page opens."""
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
