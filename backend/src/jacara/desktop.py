"""Desktop launcher (`jacara desktop`): runs the whole service for the desktop app.

The desktop app starts this command with JACARA_HOME set to its data directory. It then:

1. Selects the desktop runtime (SQLite, local files, Huey; personal mode) for itself and every
   child process, generates the launch token and the model server key, and picks free ports on
   127.0.0.1.
2. Brings the database to the current schema and marks documents interrupted by the last exit as
   failed.
3. Starts the model server (when a model file is available) and the two pipeline workers.
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
from pathlib import Path
from typing import IO, Any

READY = "JACARA_READY"
"""Prefix of the line printed once the service answers."""

WATCH_STDIN = "JACARA_WATCH_STDIN"
"""When "1", the process exits once its standard input is closed (its parent has gone)."""

STOP_SECONDS = 10.0
"""Time a child process gets to exit after SIGTERM before it is killed (as in `jacara stop`)."""

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
    """Stop child processes: SIGTERM (TerminateProcess on Windows), then kill after
    STOP_SECONDS; finally close their standard input."""
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
        if child.stdin is not None:
            child.stdin.close()


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

    children: list[subprocess.Popen[bytes]] = []
    recorded: list[dict[str, Any]] = []
    try:
        model = settings.find_model()
        if model is not None:
            from jacara.plugins.model_backends.server import server_command, server_env

            command = server_command(model, port=model_port, mmproj=settings.find_mmproj())
            with _log("model") as log:
                child = subprocess.Popen(  # noqa: S603  Our own executable and arguments.
                    command,
                    env={**server_env(), "LLAMA_API_KEY": model_key},
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            children.append(child)
            recorded.append({"pid": child.pid, "program": Path(command[0]).name})
        stages = ("read", "extract") if settings.jacara_pipeline else ("all",)
        for stage in stages:
            command = self_command("worker", "--stage", stage)
            with _log(f"worker-{stage}") as log:
                child = subprocess.Popen(  # noqa: S603  Our own executable and arguments.
                    command,
                    env={**os.environ, WATCH_STDIN: "1"},
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            children.append(child)
            recorded.append({"pid": child.pid, "program": Path(command[0]).name})
        run_file = _run_file()
        run_file.parent.mkdir(parents=True, exist_ok=True)
        run_file.write_text(
            json.dumps({"main": os.getpid(), "children": recorded}), encoding="utf-8"
        )
        _serve(token)
    finally:
        stop(children)
        _run_file().unlink(missing_ok=True)
    return 0


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
