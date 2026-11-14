"""Maintenance jobs of the desktop app: backing up, restoring a backup, moving the data folder and
restarting the background services. They run one at a time in a background thread of the service
process, report their progress to the page, and hold off requests that would interfere:

- writes: changes are refused while the job runs (a backup reads a consistent state);
- everything: every API request is refused (restoring and moving replace the database and the files
  under the service).

The job status endpoint and the health check always answer, so the page can follow the job and
reload when it ends."""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from jacara.core.i18n import render, tr

Kind = Literal["backup", "restore", "move", "restart"]
Block = Literal["writes", "everything"]

ALWAYS_OPEN = ("/api/v1/desktop/job", "/api/v1/health")
"""Paths that answer during any job: the page follows the job through them."""

LOCATION_PATH = "/api/v1/desktop/location"
"""Answers while the data folder cannot be found, so the page can retry or choose the default."""

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class JobBusy(Exception):
    """Another maintenance job is running."""


@dataclass
class Job:
    kind: Kind
    block: Block
    started: float = field(default_factory=time.time)
    stage: str = ""
    """Stored message (stored()) describing the current step."""
    done: int = 0
    total: int = 0
    finished: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    """Stored or plain message for the user when the job failed."""

    def report(self, stage: str, done: int = 0, total: int = 0) -> None:
        self.stage, self.done, self.total = stage, done, total

    def view(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "running": self.finished is None,
            "stage": render(self.stage) if self.stage else "",
            "done": self.done,
            "total": self.total,
            "started": self.started,
            "finished": self.finished,
            "result": self.result,
            "error": render(self.error) if self.error else None,
        }


class Maintenance:
    """The current or last maintenance job of this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.job: Job | None = None
        self.unavailable: Path | None = None
        """The data folder recorded in data-location.json when it cannot be found at start (an
        external drive not connected). Until it is found or the default place is chosen, only the
        location endpoints answer."""

    def running(self) -> Job | None:
        job = self.job
        return job if job is not None and job.finished is None else None

    def start(
        self,
        kind: Kind,
        block: Block,
        work: Callable[[Job], dict[str, Any] | None],
    ) -> Job:
        """Run work(job) in a background thread; its return value becomes job.result. Raises
        JobBusy when a job is already running."""
        with self._lock:
            if self.running() is not None:
                raise JobBusy
            job = Job(kind, block)
            self.job = job

        def run() -> None:
            try:
                job.result = work(job) or {}
            except Exception as err:  # Any failure ends the job and is shown to the user.
                message = getattr(err, "message", None) or str(err) or type(err).__name__
                job.error = message
                traceback.print_exc()
            finally:
                job.finished = time.time()

        threading.Thread(target=run, name=f"maintenance-{kind}", daemon=True).start()
        return job

    def refusal(self, method: str, path: str) -> str | None:
        """The reason to refuse this request now, or None."""
        if self.unavailable is not None:
            if not path.startswith("/api/") or path.startswith((*ALWAYS_OPEN, LOCATION_PATH)):
                return None
            return tr(
                "找不到数据文件夹：{folder}。如果它在移动硬盘上，请连接后重试。",
                folder=self.unavailable,
            )
        job = self.running()
        if job is None or not path.startswith("/api/") or path.startswith(ALWAYS_OPEN):
            return None
        if job.block == "writes" and method in SAFE_METHODS:
            return None
        if job.kind == "backup":
            return tr("正在备份，请稍候")
        if job.kind == "restore":
            return tr("正在从备份恢复，请稍候")
        if job.kind == "move":
            return tr("正在移动数据文件夹，请稍候")
        return tr("正在重新启动后台服务，请稍候")


MAINTENANCE = Maintenance()


class MaintenanceGate:
    """ASGI middleware: refuses requests with 503 while a maintenance job holds them off."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            reason = MAINTENANCE.refusal(scope["method"], scope["path"])
            if reason is not None:
                unavailable = MAINTENANCE.unavailable
                response = JSONResponse(
                    {
                        "detail": reason,
                        "maintenance": unavailable is None,
                        "unavailable": str(unavailable) if unavailable is not None else None,
                    },
                    status_code=503,
                    headers={"Retry-After": "2"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
