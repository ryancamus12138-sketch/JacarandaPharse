"""Desktop launcher (`jacara desktop`): starts everything in a fresh data directory, answers only
with the launch token, and leaves no process behind when the app goes away."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from jacara.desktop import READY, WATCH_STDIN

from .test_backup import png

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process checks")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(pid, 0); ps shows it with state Z.
    state = subprocess.run(  # noqa: S603
        ["ps", "-o", "stat=", "-p", str(pid)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def wait_until(condition, seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return condition()


def launch(home: Path) -> tuple[subprocess.Popen[str], str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("JACARA_", "POSTGRES_", "S3_", "MODEL_"))
    }
    env["JACARA_HOME"] = str(home)
    env[WATCH_STDIN] = "1"
    env["JACARA_LOG_DIR"] = str(home / "logs")
    process = subprocess.Popen(
        [sys.executable, "-m", "jacara.cli", "desktop"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert process.stdout is not None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if line.startswith(READY):
            return process, line.split(" ", 1)[1].strip()
        if not line and process.poll() is not None:
            break
    process.kill()
    pytest.fail("the launcher did not report ready")


def children_of(home: Path) -> list[int]:
    recorded = json.loads((home / ".run" / "desktop.json").read_text(encoding="utf-8"))
    return [child["pid"] for child in recorded["children"]]


def test_launcher_serves_with_token_and_stops_when_stdin_closes(tmp_path):
    process, enter = launch(tmp_path)
    base = enter.split("/desktop/")[0]
    try:
        assert (tmp_path / "jacara.db").is_file()
        workers = children_of(tmp_path)
        assert len(workers) == 2 and all(alive(pid) for pid in workers)
        assert httpx.get(f"{base}/api/v1/health", trust_env=False).status_code == 401
        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            assert client.get(enter).status_code == 303
            health = client.get(f"{base}/api/v1/health")
            assert health.status_code == 200 and health.json()["status"] == "ok"
            me = client.get(f"{base}/api/v1/auth/me").json()
            assert me["mode"] == "personal"
            upload = client.post(
                f"{base}/api/v1/documents",
                files={"file": ("receipt.png", png(), "image/png")},
                headers={"sec-fetch-site": "same-origin"},
            )
            assert upload.status_code == 201, upload.text
            assert list((tmp_path / "files" / "documents").iterdir())
    finally:
        assert process.stdin is not None
        process.stdin.close()  # The app closes the pipe when it quits.
    assert process.wait(timeout=60) == 0
    assert wait_until(lambda: not any(alive(pid) for pid in workers))
    assert not (tmp_path / ".run" / "desktop.json").exists()


def test_workers_exit_when_the_launcher_is_killed(tmp_path):
    process, _enter = launch(tmp_path)
    workers = children_of(tmp_path)
    process.send_signal(signal.SIGKILL)  # No chance to clean up.
    process.wait(timeout=10)
    assert wait_until(lambda: not any(alive(pid) for pid in workers))
