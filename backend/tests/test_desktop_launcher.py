"""Desktop launcher (`jacara desktop`): starts everything in a fresh data directory, answers only
with the launch token, and leaves no process behind when the app goes away."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image

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


def launch(home: Path, **extra: str) -> tuple[subprocess.Popen[str], str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("JACARA_", "POSTGRES_", "S3_", "MODEL_"))
    }
    env["JACARA_HOME"] = str(home)
    env[WATCH_STDIN] = "1"
    env["JACARA_LOG_DIR"] = str(home / "logs")
    env.update(extra)
    process = subprocess.Popen(
        [sys.executable, "-m", "jacara.cli", "desktop"],
        env=env,
        # Started from the data folder, not the project root, as the packaged app is.
        cwd=home,
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
            assert me["mode"] == "personal" and me["desktop"] is True
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


def test_a_worker_that_dies_is_started_again(tmp_path):
    process, _enter = launch(tmp_path)
    try:
        before = children_of(tmp_path)
        os.kill(before[0], signal.SIGKILL)  # As a crash in native OCR code would end it.
        assert wait_until(lambda: children_of(tmp_path)[0] != before[0])
        after = children_of(tmp_path)
        assert alive(after[0]) and after[1] == before[1]
        log = (tmp_path / "logs" / "worker-read.log").read_text(encoding="utf-8")
        assert "exited (signal SIGKILL); starting it again" in log
    finally:
        assert process.stdin is not None
        process.stdin.close()
    assert process.wait(timeout=60) == 0
    assert wait_until(lambda: not any(alive(pid) for pid in after))


def test_the_watch_folder_is_watched(tmp_path):
    watch = tmp_path / "inbox"
    watch.mkdir()
    home = tmp_path / "data"
    home.mkdir()
    process, enter = launch(home, JACARA_WATCH_DIR=str(watch))
    base = enter.split("/desktop/")[0]
    try:
        assert len(children_of(home)) == 3  # Two workers and the folder watcher.
        (watch / "receipt.png").write_bytes(png())
        with httpx.Client(trust_env=False) as client:
            client.get(enter)

            def imported() -> bool:
                items = client.get(f"{base}/api/v1/documents").json()["items"]
                return [d["filename"] for d in items] == ["receipt.png"]

            assert wait_until(imported, seconds=60)
    finally:
        assert process.stdin is not None
        process.stdin.close()
    assert process.wait(timeout=60) == 0


def finish(client: httpx.Client, base: str) -> dict:
    def done() -> bool:
        job = client.get(f"{base}/api/v1/desktop/job").json()
        return job is not None and not job["running"]

    assert wait_until(done, seconds=120)
    return client.get(f"{base}/api/v1/desktop/job").json()


SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def test_a_moved_data_folder_that_is_missing_waits_for_it(tmp_path):
    start = tmp_path / "app"
    start.mkdir()
    away = tmp_path / "usb" / "JacarandaParse"
    (start / "data-location.json").write_text(json.dumps({"path": str(away)}), "utf-8")
    process, enter = launch(start)
    base = enter.split("/desktop/")[0]
    try:
        with httpx.Client(trust_env=False) as client:
            client.get(enter)
            assert client.get(f"{base}/api/v1/health").status_code == 200
            refused = client.get(f"{base}/api/v1/documents")
            assert refused.status_code == 503 and refused.json()["unavailable"] == str(away)
            assert client.get(f"{base}/api/v1/desktop/location").json()["unavailable"] == str(away)
            assert (
                not (start / "jacara.db").exists()
                and not (start / ".run" / "desktop.json").exists()
            )
            retry = client.post(f"{base}/api/v1/desktop/location/retry", headers=SAME_ORIGIN)
            assert retry.status_code == 409
            away.mkdir(parents=True)  # A folder at that place, but not the data.
            retry = client.post(f"{base}/api/v1/desktop/location/retry", headers=SAME_ORIGIN)
            assert retry.status_code == 409
            (away / "jacara.db").touch()  # The drive with the data is connected.
            retry = client.post(f"{base}/api/v1/desktop/location/retry", headers=SAME_ORIGIN)
            assert retry.status_code == 200 and retry.json()["unavailable"] is None
            assert client.get(f"{base}/api/v1/documents").status_code == 200
            assert (away / "jacara.db").is_file() and len(children_of(away)) == 2
    finally:
        assert process.stdin is not None
        process.stdin.close()
    assert process.wait(timeout=60) == 0


def test_backup_restart_and_move_with_the_running_app(tmp_path):
    start = tmp_path / "app"
    start.mkdir()
    usb = tmp_path / "usb"
    usb.mkdir()
    process, enter = launch(start)
    base = enter.split("/desktop/")[0]
    try:
        with httpx.Client(trust_env=False) as client:
            client.get(enter)
            upload = client.post(
                f"{base}/api/v1/documents",
                files={"file": ("receipt.png", png(), "image/png")},
                headers=SAME_ORIGIN,
            )
            assert upload.status_code == 201, upload.text
            workers = children_of(start)
            made = client.post(f"{base}/api/v1/desktop/backups", json={}, headers=SAME_ORIGIN)
            assert made.status_code == 202, made.text
            backup = finish(client, base)
            assert backup["error"] is None and Path(backup["result"]["path"]).is_file()
            assert all(alive(pid) for pid in children_of(start))  # Paused workers run again.
            assert set(children_of(start)).isdisjoint(workers)

            restarted = client.post(f"{base}/api/v1/desktop/restart", headers=SAME_ORIGIN)
            assert restarted.status_code == 202 and finish(client, base)["error"] is None
            assert not any(alive(pid) for pid in workers)

            moved = client.post(
                f"{base}/api/v1/desktop/move", json={"parent": str(usb)}, headers=SAME_ORIGIN
            )
            assert moved.status_code == 202, moved.text
            assert finish(client, base)["error"] is None
            target = usb / "JacarandaParse"
            assert (target / "jacara.db").is_file() and not (start / "jacara.db").exists()
            assert all(alive(pid) for pid in children_of(target))
            # The process record moved with the data; none is left where the data was.
            assert not (start / ".run" / "desktop.json").exists()
            items = client.get(f"{base}/api/v1/documents").json()["items"]
            assert [d["filename"] for d in items] == ["receipt.png"]
            original = client.get(f"{base}/api/v1/documents/{items[0]['id']}/file")
            assert original.status_code == 200 and original.content == png()
    finally:
        assert process.stdin is not None
        process.stdin.close()
    assert process.wait(timeout=60) == 0

    # The next start finds the data where it was moved.
    process, enter = launch(start)
    base = enter.split("/desktop/")[0]
    try:
        with httpx.Client(trust_env=False) as client:
            client.get(enter)
            items = client.get(f"{base}/api/v1/documents").json()["items"]
            assert [d["filename"] for d in items] == ["receipt.png"]
            assert client.get(f"{base}/api/v1/desktop/data").json()["path"] == str(target)
    finally:
        assert process.stdin is not None
        process.stdin.close()
    assert process.wait(timeout=60) == 0


def test_several_watch_folders_are_watched(tmp_path):
    first, second = tmp_path / "scans", tmp_path / "mail"
    first.mkdir()
    second.mkdir()
    home = tmp_path / "data"
    (home / "data").mkdir(parents=True)
    folders = [{"path": str(first), "template": "auto"}, {"path": str(second), "template": "auto"}]
    (home / "data" / "settings.json").write_text(
        json.dumps({"jacara_watch_folders": folders}), "utf-8"
    )
    process, enter = launch(home)
    base = enter.split("/desktop/")[0]
    try:
        assert len(children_of(home)) == 3  # Two workers and one watcher for both folders.
        (first / "a.png").write_bytes(png())
        other = io.BytesIO()  # Different content: the same file twice is imported once.
        Image.new("RGB", (40, 20), "black").save(other, format="PNG")
        (second / "b.png").write_bytes(other.getvalue())
        with httpx.Client(trust_env=False) as client:
            client.get(enter)

            def imported() -> bool:
                items = client.get(f"{base}/api/v1/documents").json()["items"]
                return sorted(d["filename"] for d in items) == ["a.png", "b.png"]

            assert wait_until(imported, seconds=60)
    finally:
        assert process.stdin is not None
        process.stdin.close()
    assert process.wait(timeout=60) == 0
