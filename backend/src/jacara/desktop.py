"""Desktop launcher (`jacara desktop`): runs the whole service for the desktop app.

The desktop app starts this command with JACARA_HOME set to its data directory. It then:

1. Selects the desktop runtime (SQLite, local files, Huey; personal mode) for itself and every
   child process, generates the launch token and the model server key, and picks free ports on
   127.0.0.1.
2. Brings the database to the current schema and marks documents interrupted by the last exit as
   failed.
3. Starts the model server (when a model file is available) and the two pipeline workers, and
   keeps them running: a child that exits on its own is started again (see Supervisor).
4. Serves the API and web pages, and once they answer prints one line to standard output:
   `JACARA_READY http://127.0.0.1:<port>/desktop/enter?token=<token>`; the app opens that address.

Shutdown: the app closes this process's standard input (JACARA_WATCH_STDIN=1) or sends SIGTERM;
the web service stops, then the workers and the model server. So that no process is left behind
when a parent dies abruptly:
- each worker exits when its standard input (a pipe held by this process) closes;
- on Windows this process and all its children are in a job object with
  JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, so the system ends them all when this process ends;
- on macOS and Linux the process ids are recorded in .run/desktop.json, and processes left from an
  earlier run are stopped at the next start."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from jacara.core.queue import TaskQueue

READY = "JACARA_READY"
"""Prefix of the line printed once the service answers."""

WATCH_STDIN = "JACARA_WATCH_STDIN"
"""When "1", the process exits once its standard input is closed (its parent has gone)."""

STOP_SECONDS = 10.0
"""Time a child process gets to exit after SIGTERM before it is killed (as in `jacara stop`)."""

START_SECONDS = 1.0
"""A child that exits sooner than this after being started has failed to start (supervisord's
startsecs, default 1)."""

START_RETRIES = 3
"""Failed starts in a row after which a child is given up (supervisord's startretries, default 3).
Before failed start n the supervisor waits n seconds, as supervisord's backoff does."""

BACKOFF_SECONDS = 1.0
"""Wait before retrying failed start n: n times this (supervisord: n seconds)."""

POLL_SECONDS = 1.0
"""How often the supervisor checks its children."""

RUNTIME = {
    "JACARA_MODE": "personal",
    "JACARA_DATABASE": "sqlite",
    "JACARA_STORAGE": "local",
    "JACARA_QUEUE": "huey",
    "JACARA_API_DOCS": "false",
}
"""Settings fixed for the desktop edition (ADR 0068), passed to every child process."""


def self_command(*args: str) -> list[str]:
    """Command that runs a jacara subcommand with this same program. In the packaged app
    sys.executable is the app's own executable rather than a Python interpreter."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "jacara.cli", *args]


def reserve_port() -> socket.socket:
    """A socket bound to a free port on 127.0.0.1, chosen by the system (port 0)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def free_port() -> int:
    """A port that was free a moment ago, for a child process that binds it itself."""
    with reserve_port() as sock:
        return int(sock.getsockname()[1])


def on_stdin_closed(action: Callable[[], None]) -> None:
    """Call action once standard input reaches end of file (the parent closed its end or exited)."""

    def watch() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        action()

    threading.Thread(target=watch, name="stdin-watch", daemon=True).start()


def kill_children_with_me() -> Any:
    """Windows: put this process into a job object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
    Processes started afterwards join the same job (CreateProcess default), and the system ends
    every process of the job when its last handle closes, which happens when this process ends.
    Returns the job handle, which must stay referenced; None on other systems or on failure."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_extended_limit_information = 9
    job_object_limit_kill_on_job_close = 0x2000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_job_close
    if not kernel32.SetInformationJobObject(
        job, job_object_extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)
    ):
        return None
    if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        return None
    return job


def _run_file() -> Path:
    from jacara.core.config import data_home

    return data_home() / ".run" / "desktop.json"


def _command_of(pid: int) -> str:
    """Command line of a running process (POSIX ps), empty when it is not running."""
    try:
        result = subprocess.run(  # noqa: S603
            ["ps", "-o", "command=", "-p", str(pid)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip()


def stop_previous_run() -> None:
    """macOS and Linux: stop processes left by a previous run that ended abruptly. A recorded id is
    acted on only while its command line still contains the recorded program, so a reused process
    id of an unrelated program is never touched."""
    import signal

    path = _run_file()
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if sys.platform != "win32":
        for child in recorded.get("children", []):
            pid, program = int(child.get("pid", 0)), str(child.get("program", ""))
            if pid > 0 and program and program in _command_of(pid):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
    path.unlink(missing_ok=True)


def stop(children: list[subprocess.Popen[bytes]]) -> None:
    """Stop child processes: close their standard input first (workers exit at once, see
    WATCH_STDIN, instead of finishing the task in hand), then SIGTERM (TerminateProcess on
    Windows), then kill whatever is still running after STOP_SECONDS. Documents left in progress
    are marked as interrupted at the next start."""
    for child in children:
        if child.stdin is not None:
            with contextlib.suppress(OSError):
                child.stdin.close()
    for child in children:
        if child.poll() is None:
            child.terminate()
    deadline = time.monotonic() + STOP_SECONDS
    for child in children:
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def _describe_exit(code: int) -> str:
    """Exit status in words: a negative code is the signal that ended the process (Popen)."""
    if code < 0:
        import signal

        try:
            return f"signal {signal.Signals(-code).name}"
        except ValueError:
            return f"signal {-code}"
    return f"exit code {code}"


@dataclass
class Child:
    """A child process the launcher starts and keeps running."""

    name: str
    """Log file name in the logs folder (model, worker-read, ...)."""
    command: list[str]
    env: dict[str, str]
    watch_stdin: bool = False
    """Give the child a pipe as standard input; with JACARA_WATCH_STDIN=1 it exits when the pipe
    closes, that is when the launcher is gone."""
    after_restart: Callable[[], object] | None = None
    """Called after the child was started again, for example to queue work it lost."""
    process: subprocess.Popen[bytes] | None = None
    started: float = 0.0
    failures: int = 0
    """Failed starts in a row (exits within START_SECONDS)."""
    retry_at: float | None = None
    given_up: bool = False

    def start(self) -> None:
        with _log(self.name) as log:
            self.process = subprocess.Popen(  # noqa: S603  Our own executable and arguments.
                self.command,
                env=self.env,
                stdin=subprocess.PIPE if self.watch_stdin else subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        self.started = time.monotonic()

    def note(self, text: str) -> None:
        """Append a line from the launcher to this child's log."""
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with _log(self.name) as log:
            log.write(f"[{stamp}] jacara desktop: {text}\n".encode())


class Supervisor:
    """Starts the children and starts again any child that exits on its own, as supervisord does
    with autorestart: a child that ran at least START_SECONDS is restarted at once; one that exits
    sooner has failed to start and is retried after 1, 2, 3 seconds, then given up after
    START_RETRIES failures in a row. Each exit and restart is noted in the child's log.

    Without this, a worker that crashed (for example in native OCR code) would leave documents
    queued forever while the app looks fine."""

    def __init__(self, children: list[Child], record: Callable[[list[Child]], None]) -> None:
        self.children = children
        self.record = record
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._watch, name="supervisor", daemon=True)

    def start(self) -> None:
        for child in self.children:
            child.start()
        self.record(self.children)
        self.thread.start()

    def stop(self) -> None:
        """Stop supervising, then stop the children."""
        self.stopping.set()
        if self.thread.is_alive():
            self.thread.join()
        stop([c.process for c in self.children if c.process is not None])

    def _watch(self) -> None:
        while not self.stopping.wait(POLL_SECONDS):
            for child in self.children:
                self.check(child)

    def check(self, child: Child) -> None:
        """Restart child if it has exited and is due; called once per poll."""
        now = time.monotonic()
        if child.given_up or child.process is None:
            return
        if child.retry_at is None:
            code = child.process.poll()
            if code is None:
                if now - child.started >= START_SECONDS:
                    child.failures = 0  # Running: the next exit is not a failed start.
                return
            if child.process.stdin is not None:
                child.process.stdin.close()
            if now - child.started < START_SECONDS:
                child.failures += 1
            else:
                child.failures = 0
            if child.failures > START_RETRIES:
                child.given_up = True
                child.note(
                    f"exited ({_describe_exit(code)}) {child.failures} times right after "
                    "starting; not starting it again until the app restarts"
                )
                return
            child.retry_at = now + child.failures * BACKOFF_SECONDS
            child.note(f"exited ({_describe_exit(code)}); starting it again")
        if now < child.retry_at or self.stopping.is_set():
            return
        child.retry_at = None
        child.start()
        self.record(self.children)
        if child.after_restart is not None:
            try:
                child.after_restart()
            except Exception as err:  # Recovery must never stop supervision.
                child.note(f"could not queue waiting documents again: {err}")


def requeue_waiting(
    factory: sessionmaker[Session] | None = None, queue: TaskQueue | None = None
) -> int:
    """Queue every document still waiting (uploaded) again. Huey removes a task from the queue
    when a worker takes it, so a worker that dies mid-task loses that task; queuing waiting
    documents again recovers them. A document queued twice is processed once: the second task
    finds it no longer waiting and skips it (pipeline.process._claim)."""
    from sqlalchemy import select

    from jacara.core.db import session_scope
    from jacara.core.queue import create_queue
    from jacara.models import Document

    with session_scope(factory) as session:
        ids = list(
            session.scalars(
                select(Document.id)
                .where(Document.status == "uploaded")
                .order_by(Document.created_at, Document.id)
            )
        )
    queue = queue or create_queue()
    for document_id in ids:
        queue.enqueue_document(document_id)
    return len(ids)


def _log(name: str) -> IO[bytes]:
    from jacara.core.config import data_home

    folder = Path(os.environ.get("JACARA_LOG_DIR") or data_home() / "logs")
    folder.mkdir(parents=True, exist_ok=True)
    return (folder / f"{name}.log").open("ab")


def run_desktop() -> int:
    """Run the desktop service until the app closes standard input or a stop signal arrives."""
    if not os.environ.get("JACARA_HOME"):
        print("JACARA_HOME (the data directory) is not set", file=sys.stderr)
        return 2
    os.environ.update(RUNTIME)
    token = secrets.token_urlsafe(32)
    model_key = secrets.token_urlsafe(32)
    model_port = free_port()
    # Children inherit these through the environment; nothing secret is written to disk or passed
    # on a command line (command lines are visible to every user in ps).
    os.environ["JACARA_DESKTOP_TOKEN"] = token
    os.environ["MODEL_API_KEY"] = model_key
    os.environ["MODEL_SERVER"] = f"http://127.0.0.1:{model_port}"

    from jacara.core.config import get_settings

    get_settings.cache_clear()
    job = kill_children_with_me()  # noqa: F841  Kept referenced for the lifetime of the process.
    stop_previous_run()
    settings = get_settings()

    from jacara.ops.commands import migrate_to
    from jacara.pipeline.process import reset_interrupted

    migrate_to(settings.database_url, None)
    reset_interrupted()

    children: list[Child] = []
    model = settings.find_model()
    if model is not None:
        from jacara.plugins.model_backends.server import server_command, server_env

        command = server_command(model, port=model_port, mmproj=settings.find_mmproj())
        children.append(Child("model", command, {**server_env(), "LLAMA_API_KEY": model_key}))
    stages = ("read", "extract") if settings.jacara_pipeline else ("all",)
    for stage in stages:
        children.append(
            Child(
                f"worker-{stage}",
                self_command("worker", "--stage", stage),
                {**os.environ, WATCH_STDIN: "1"},
                watch_stdin=True,
                # The stage that takes uploaded documents may have lost the one it was on.
                after_restart=requeue_waiting if stage in ("read", "all") else None,
            )
        )
    supervisor = Supervisor(children, _record)
    try:
        supervisor.start()
        # Tasks lost when the last run ended in the middle of one.
        requeue_waiting()
        _serve(token)
    finally:
        supervisor.stop()
        _run_file().unlink(missing_ok=True)
    return 0


def _record(children: list[Child]) -> None:
    """Write the process ids to .run/desktop.json (see stop_previous_run)."""
    recorded = [
        {"pid": c.process.pid, "program": Path(c.command[0]).name}
        for c in children
        if c.process is not None
    ]
    run_file = _run_file()
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(json.dumps({"main": os.getpid(), "children": recorded}), encoding="utf-8")


def _serve(token: str) -> None:
    """Serve the API and web pages on a port reserved before start-up, print the ready line once
    they answer, and return when the service stops."""
    import uvicorn

    from jacara.core.local_guard import ENTER_PATH

    sock = reserve_port()
    port = int(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config("jacara.app:app", log_level="info"))

    def announce() -> None:
        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.started:
            print(f"{READY} http://127.0.0.1:{port}{ENTER_PATH}?token={token}", flush=True)

    threading.Thread(target=announce, name="announce", daemon=True).start()
    if os.environ.get(WATCH_STDIN) == "1":
        on_stdin_closed(lambda: setattr(server, "should_exit", True))
    server.run(sockets=[sock])
