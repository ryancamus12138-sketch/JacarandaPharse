"""Desktop launcher supervision: a child that exits on its own is started again, one that keeps
failing right after starting is given up, and documents whose task was lost are queued again."""

from __future__ import annotations

import sys
import time
import uuid

import pytest

from jacara import desktop
from jacara.core.db import session_scope
from jacara.core.owner import PERSONAL_OWNER
from jacara.desktop import Child, Supervisor, requeue_waiting
from jacara.models import Document


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "START_SECONDS", 0.3)
    monkeypatch.setattr(desktop, "BACKOFF_SECONDS", 0.05)
    monkeypatch.setattr(desktop, "POLL_SECONDS", 0.05)
    monkeypatch.setenv("JACARA_LOG_DIR", str(tmp_path / "logs"))


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def run_until(supervisor: Supervisor, condition, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for child in supervisor.children:
            supervisor.check(child)
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_a_child_that_dies_is_started_again(tmp_path):
    restarted: list[int] = []
    # Runs past START_SECONDS (it started fine), then exits on its own.
    child = Child(
        "worker-read",
        python("import time; time.sleep(0.5)"),
        {},
        after_restart=lambda: restarted.append(1),
    )
    recorded: list[list[int]] = []
    supervisor = Supervisor(
        [child], lambda cs: recorded.append([c.process.pid for c in cs if c.process])
    )
    for c in supervisor.children:
        c.start()
    first = child.process
    assert first is not None
    try:
        assert run_until(supervisor, lambda: child.process is not first)
        assert restarted == [1]
        assert child.failures == 0 and not child.given_up
        assert child.process is not None
        assert recorded[-1] == [child.process.pid] != [first.pid]
        log = (tmp_path / "logs" / "worker-read.log").read_text(encoding="utf-8")
        assert "jacara desktop: exited (exit code 0); starting it again" in log
    finally:
        supervisor.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="exit status by signal is POSIX")
def test_the_signal_that_ended_a_child_is_logged(tmp_path):
    child = Child("model", python("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"), {})
    supervisor = Supervisor([child], lambda _cs: None)
    child.start()
    child.started -= 10  # Pretend it had been running for a while.
    try:
        log = tmp_path / "logs" / "model.log"
        assert run_until(supervisor, lambda: "exited (signal SIGKILL)" in log.read_text())
    finally:
        supervisor.stop()


def test_a_child_that_keeps_failing_to_start_is_given_up(tmp_path):
    child = Child("worker-extract", python("raise SystemExit(3)"), {})
    supervisor = Supervisor([child], lambda _cs: None)
    child.start()
    try:
        assert run_until(supervisor, lambda: child.given_up)
        assert child.failures == desktop.START_RETRIES + 1
        log = (tmp_path / "logs" / "worker-extract.log").read_text(encoding="utf-8")
        assert log.count("; starting it again") == desktop.START_RETRIES
        assert "not starting it again until the app restarts" in log
        # Given up: no further start.
        pid = child.process.pid if child.process else None
        run_until(supervisor, lambda: False, seconds=0.3)
        assert (child.process.pid if child.process else None) == pid
    finally:
        supervisor.stop()


def test_stop_ends_the_children_and_stops_restarting():
    child = Child("worker-read", python("import time; time.sleep(30)"), {}, watch_stdin=True)
    supervisor = Supervisor([child], lambda _cs: None)
    supervisor.start()
    supervisor.stop()
    assert child.process is not None and child.process.poll() is not None
    assert not supervisor.thread.is_alive()


class Queued:
    def __init__(self) -> None:
        self.ids: list[uuid.UUID] = []

    def enqueue_document(self, document_id: uuid.UUID) -> None:
        self.ids.append(document_id)

    def enqueue_eval(self, run_id: uuid.UUID) -> None:
        raise AssertionError


def document(factory, status: str) -> uuid.UUID:
    document_id = uuid.uuid4()
    with session_scope(factory) as session:
        session.add(
            Document(
                id=document_id,
                filename="receipt.jpg",
                content_type="image/jpeg",
                sha256="0" * 64,
                size_bytes=10,
                page_count=1,
                storage_key=f"documents/{document_id}/receipt",
                status=status,
                user_id=PERSONAL_OWNER.user_id,
                team_id=PERSONAL_OWNER.team_id,
            )
        )
    return document_id


def test_waiting_documents_are_queued_again(factory):
    first = document(factory, "uploaded")
    second = document(factory, "uploaded")
    document(factory, "done")
    document(factory, "processing")
    queue = Queued()
    assert requeue_waiting(factory, queue) == 2
    assert sorted(queue.ids) == sorted([first, second])
