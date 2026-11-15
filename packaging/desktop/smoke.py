"""Smoke test of the packaged backend (PyInstaller output): start it as the desktop app does, from
an unrelated folder with an empty data folder, and check what packaging can break.

    python packaging/desktop/smoke.py dist/jacara-backend/jacara-backend sample.pdf sample.jpg

Checks, each of which fails when a file is missing from the package:
- the database is created by the packaged migrations;
- the interface translations load (English answers);
- the starter templates, the table model (shipped with the program), RapidOCR's models and the list
  of models offered for download are found;
- About and diagnostics: LICENSE, NOTICE and THIRD_PARTY_LICENSES are shipped and readable, the
  shipped llama-server runs (its version is in the diagnostics) and the diagnostics name no folder
  under the home folder;
- a PDF passes OCR (PDF rendering, OpenCV, ONNX Runtime, layout model);
- the watch folder imports a file placed in it;
- a download asked to be saved lands in the Downloads folder;
- a full backup is made, checked and restored through the maintenance jobs, and an automatic
  database backup (SQLite online backup) is made and restored;
- closing standard input stops everything.
No model file is given, so no model server starts and extraction itself is not exercised.

The backend's output is read all the time in a thread, as the desktop shell does (on Windows a
pipe holds only a few kilobytes, and a backend whose output nobody reads stops at its next line).
When a check fails, its output and the log files are printed, so a failure on a build machine can
be understood from the build log."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import IO

import httpx

OUTPUT: list[str] = []
"""Everything the backend has printed (standard output and standard error)."""

LOGS: list[Path] = []
"""Folders whose files are printed after a failure."""


def show_output() -> None:
    print("---- backend output ----", flush=True)
    print("".join(OUTPUT[-400:]).rstrip() or "(nothing)", flush=True)
    for folder in LOGS:
        for log in sorted(folder.glob("*.log")) if folder.is_dir() else []:
            print(f"---- {log.name} ----", flush=True)
            text = log.read_text("utf-8", errors="replace")
            print(text[-20000:].rstrip() or "(empty)", flush=True)


def check(ok: bool, what: str) -> None:
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
        show_output()
        raise SystemExit(1)


def main() -> None:
    exe, pdf, image = (Path(a).resolve() for a in sys.argv[1:4])
    root = Path(tempfile.mkdtemp(prefix="jacara-smoke-"))
    data, cwd, watch, home = (root / n for n in ("data", "cwd", "watch", "home"))
    for folder in (data, cwd, watch, home):
        folder.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JACARA_", "MODEL_"))}
    env.update(
        JACARA_HOME=str(data),
        JACARA_WATCH_STDIN="1",
        JACARA_WATCH_DIR=str(watch),
        # Each background process writes the stacks of its threads to its log every 40 seconds,
        # so a process that hangs shows where (printed with the logs when a check fails).
        JACARA_STACK_DUMP_SECONDS="40",
        HOME=str(home),
        USERPROFILE=str(home),
    )
    LOGS.append(data / "logs")
    process = subprocess.Popen(  # noqa: S603
        [str(exe), "desktop"],
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout is None or process.stdin is None:
        raise SystemExit("no pipes to the backend")
    ready = threading.Event()
    found: list[str] = []

    def read(stream: IO[str]) -> None:
        for line in stream:
            OUTPUT.append(line)
            if not found and (match := re.search(r"JACARA_READY (\S+)", line)):
                found.append(match.group(1))
                ready.set()
        ready.set()  # The backend ended.

    threading.Thread(target=read, args=(process.stdout,), name="output", daemon=True).start()
    ready.wait(timeout=120)
    if not found:
        exited = process.poll()
        print(f"backend {'exited with ' + str(exited) if exited is not None else 'still running'}")
        check(False, "started and reported ready (database migrated)")
        return
    enter = found[0]
    check(True, "started and reported ready (database migrated)")
    base = enter.split("/desktop/")[0]
    client = httpx.Client(base_url=base, follow_redirects=True, timeout=60, trust_env=False)
    try:
        client.get(enter)
        me = client.get("/api/v1/auth/me", headers={"Accept-Language": "en"}).json()
        check(me["desktop"] is True and me["shown_name"] == "Me", "translations load")
        check(len(client.get("/api/v1/templates/starters").json()) > 0, "starter templates")
        info = client.get("/api/v1/system").json()
        check(info["structure"]["table"] == "slanet-plus.onnx", "table model shipped")
        check(
            info["license"] == "Apache-2.0" and "JacarandaParse" in info["notice"],
            "NOTICE shipped",
        )
        texts = {
            which: client.get(f"/api/v1/system/licenses/{which}")
            for which in ("license", "notice", "third_party")
        }
        check(
            info["third_party"] is True
            and all(r.status_code == 200 for r in texts.values())
            and "Apache License" in texts["license"].json()["text"]
            and "Python packages" in texts["third_party"].json()["text"],
            "licence texts shipped",
        )
        report = client.get("/api/v1/system/diagnostics").json()["text"]
        check(
            "Model server: " in report and "(version: " in report,
            "shipped llama-server runs (diagnostics)",
        )
        check(str(home) not in report, "diagnostics write the home folder as ~")
        # The packaged program runs in UTF-8 mode (jacara.spec) on every system; without it the
        # backend and its workers write their output in the system code page on Windows.
        check("UTF-8 mode on" in report, "Python runs in UTF-8 mode")
        setup = client.get("/api/v1/setup").json()
        listed = [f["filename"] for f in setup["files"]]
        wanted = ["Qwen3.5-9B-Q4_K_M.gguf", "mmproj-F16.gguf"]
        check(setup["needs_model"] is True and listed == wanted, "model download list shipped")
        saved = client.get(
            "/api/v1/templates/receipt/export?format=xlsx", headers={"X-Jacara-Save": "downloads"}
        ).json()
        check(Path(saved["path"]).is_file(), "download saved to the Downloads folder")
        shutil.copy(image, watch / image.name)
        upload = client.post(
            "/api/v1/documents",
            files={"file": (pdf.name, pdf.read_bytes(), "application/pdf")},
            headers={"sec-fetch-site": "same-origin"},
        )
        document = upload.json()["id"]
        read = imported = False
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not (read and imported):
            detail = client.get(f"/api/v1/documents/{document}").json()
            # Past OCR: handed to the model stage (progress 10 and more), or failed there for want
            # of a model server.
            at_model = detail["status"] == "failed" and "模型服务" in (detail.get("error") or "")
            read = read or (detail.get("progress") or 0) >= 10 or at_model
            names = [d["filename"] for d in client.get("/api/v1/documents").json()["items"]]
            imported = image.name in names
            time.sleep(1)
        check(read, "PDF passes OCR")
        if not imported:
            # What the watcher should have seen, and how the app reports its processes.
            now = time.time()
            for path in sorted(watch.rglob("*")):
                stat = path.stat()
                print(
                    f"watch folder: {path} {stat.st_size} bytes, "
                    f"modified {now - stat.st_mtime:.0f} s ago"
                )
            print(client.get("/api/v1/system/diagnostics").json()["text"])
        check(imported, "watch folder imports")
        same_origin = {"sec-fetch-site": "same-origin"}

        def job(path: str, body: dict[str, object] | None = None) -> dict:
            started = client.post(path, json=body, headers=same_origin)
            if started.status_code != 202:
                return {"error": started.text}
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                current = client.get("/api/v1/desktop/job").json()
                if not current["running"]:
                    return current
                time.sleep(0.5)
            return {"error": "timed out"}

        made = job("/api/v1/desktop/backups", {"models": False})
        archive = (made.get("result") or {}).get("path")
        check(
            made.get("error") is None and bool(archive) and Path(archive).is_file(), "full backup"
        )
        restored = job("/api/v1/desktop/restore", {"path": archive})
        check(restored.get("error") is None, "full backup restored and checked")
        names = [d["filename"] for d in client.get("/api/v1/documents").json()["items"]]
        check(pdf.name in names, "documents back after restoring")
        # A database backup as the automatic backup makes it (SQLite online backup).
        auto = data / "backups" / "auto"
        auto.mkdir(parents=True, exist_ok=True)
        with (
            contextlib.closing(sqlite3.connect(data / "jacara.db")) as source,
            contextlib.closing(sqlite3.connect(auto / "jacara-smoke.db")) as copy,
        ):
            source.backup(copy)
        restored = job("/api/v1/desktop/restore", {"path": str(auto / "jacara-smoke.db")})
        check(restored.get("error") is None, "automatic database backup restored")
        listed = client.get("/api/v1/desktop/data").json()["backups"]
        check({b["kind"] for b in listed} == {"full", "database"}, "backups listed")
    finally:
        process.stdin.close()
    check(process.wait(timeout=60) == 0, "stops when standard input closes")
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
