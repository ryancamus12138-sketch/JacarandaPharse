"""Smoke test of the packaged backend (PyInstaller output): start it as the desktop app does, from
an unrelated folder with an empty data folder, and check what packaging can break.

    python packaging/desktop/smoke.py dist/jacara-backend/jacara-backend sample.pdf sample.jpg

Checks, each of which fails when a file is missing from the package:
- the database is created by the packaged migrations;
- the interface translations load (English answers);
- the starter templates, the table model (shipped with the program) and RapidOCR's models are found;
- a PDF passes OCR (PDF rendering, OpenCV, ONNX Runtime, layout model);
- the watch folder imports a file placed in it;
- a download asked to be saved lands in the Downloads folder;
- a full backup is made, checked and restored through the maintenance jobs, and an automatic
  database backup (SQLite online backup) is made and restored;
- closing standard input stops everything.
No model file is given, so no model server starts and extraction itself is not exercised."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def check(ok: bool, what: str) -> None:
    print(("ok    " if ok else "FAIL  ") + what, flush=True)
    if not ok:
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
        HOME=str(home),
        USERPROFILE=str(home),
    )
    process = subprocess.Popen(  # noqa: S603
        [str(exe), "desktop"],
        env=env,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if process.stdout is None or process.stdin is None:
        raise SystemExit("no pipes to the backend")
    enter = None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        found = re.search(r"JACARA_READY (\S+)", line)
        if found:
            enter = found.group(1)
            break
    if enter is None:
        check(False, "started and reported ready (database migrated)")
        return
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
