"""Saving downloads and opening things with the system, for the desktop app.

A browser saves a download to the user's Downloads folder by itself. The desktop app shows its pages
in the system web view, which does not: WKWebView on macOS ignores a link with the download
attribute unless the app handles the download itself (tauri-apps/tauri#4633). So that exports work
the same in every web view, the desktop app saves downloads in the service, which runs on the same
computer:

- SaveToDownloads (ASGI middleware): a request carrying the header X-Jacara-Save: downloads gets
  its file response written to the Downloads folder instead of returned; the answer says where.
- save_download(): a file made in the page (for example a CSV) is sent here and saved the same way.
- reveal(): shows a saved file in Finder or File Explorer.
- open_url(): opens a web address in the default browser (Python's webbrowser module), for links
  that would otherwise open inside the app window.

File names follow what browsers do: characters that file systems refuse are replaced, and an
existing name gets " (1)", " (2)" ... before the extension."""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import IO
from urllib.parse import unquote, urlsplit

import anyio
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAVE_HEADER = "x-jacara-save"
"""Request header asking for the file response to be saved; its value is downloads."""

UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
"""Characters refused in file names on Windows; / and the control characters also on macOS."""

RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
"""Windows device names, which cannot be used as file names whatever the extension."""


class DesktopFileError(Exception):
    pass


def downloads_folder() -> Path:
    """The user's Downloads folder (platformdirs): on Windows the known folder FOLDERID_Downloads,
    which the user may have moved; on macOS ~/Downloads."""
    import platformdirs

    return platformdirs.user_downloads_path()


def safe_name(name: str) -> str:
    """A file name every supported system accepts: no folder part, no refused characters, no
    Windows device name, no trailing dots or spaces."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = UNSAFE.sub("_", name).strip().rstrip(". ")
    stem = name.split(".", 1)[0]
    if not name or stem.upper() in RESERVED:
        name = f"_{name}" if name else "download"
    return name[:200]


def unique_path(folder: Path, name: str) -> Path:
    """folder/name, or folder/"stem (n).ext" with the first n not yet taken."""
    target = folder / name
    if not target.exists():
        return target
    stem, dot, suffix = name.rpartition(".")
    if not dot or not stem:
        stem, suffix = name, ""
    n = 1
    while True:
        candidate = folder / (f"{stem} ({n}).{suffix}" if suffix else f"{stem} ({n})")
        if not candidate.exists():
            return candidate
        n += 1


def filename_of(disposition: str) -> str | None:
    """File name from a Content-Disposition header: filename*=UTF-8''... (RFC 6266, preferred),
    otherwise filename="..."."""
    encoded = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, re.IGNORECASE)
    if encoded:
        return unquote(encoded.group(1).strip())
    plain = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.IGNORECASE)
    return plain.group(1).strip() if plain else None


class Saving:
    """A download being written: into a hidden temporary file in the Downloads folder, renamed to
    its final unique name when complete, so a half-written file never carries the real name."""

    def __init__(self, name: str, folder: Path | None = None) -> None:
        self.folder = folder or downloads_folder()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.name = safe_name(name)
        self.temporary = self.folder / f".{uuid.uuid4().hex}.jacara-download"
        self.file: IO[bytes] = self.temporary.open("xb")

    def write(self, data: bytes) -> None:
        self.file.write(data)

    def finish(self) -> Path:
        self.file.close()
        target = unique_path(self.folder, self.name)
        os.replace(self.temporary, target)
        return target

    def abandon(self) -> None:
        self.file.close()
        with contextlib.suppress(OSError):
            self.temporary.unlink()


def save_download(name: str, data: bytes, folder: Path | None = None) -> Path:
    """Save a file made in the page to the Downloads folder; returns where it went."""
    saving = Saving(name, folder)
    try:
        saving.write(data)
        return saving.finish()
    except BaseException:
        saving.abandon()
        raise


def reveal(path: Path, folder: Path | None = None) -> None:
    """Show a saved download selected in Finder or File Explorer. Only files in the Downloads folder
    are shown, so the page cannot use this to open anything else."""
    base = (folder or downloads_folder()).resolve()
    target = path.resolve()
    if target.parent != base or not target.is_file():
        raise DesktopFileError(str(path))
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(target)], check=False)  # noqa: S603, S607
    elif sys.platform == "win32":
        # explorer returns 1 even on success; its result is not checked.
        subprocess.run(["explorer", f"/select,{target}"], check=False)  # noqa: S603, S607
    else:
        subprocess.run(["xdg-open", str(base)], check=False)  # noqa: S603, S607


def open_folder(path: Path) -> None:
    """Open a folder in Finder or File Explorer."""
    if not path.is_dir():
        raise DesktopFileError(str(path))
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)  # noqa: S603, S607
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.run(["xdg-open", str(path)], check=False)  # noqa: S603, S607


def _applescript_text(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


POWERSHELL_FOLDER = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:JACARA_PROMPT
if ($dialog.ShowDialog($owner) -eq 'OK') { $dialog.SelectedPath }
"""

POWERSHELL_FILE = """
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = $env:JACARA_PROMPT
$dialog.Filter = $env:JACARA_FILTER
if ($dialog.ShowDialog($owner) -eq 'OK') { $dialog.FileName }
"""


def _ask(command: list[str], env: dict[str, str] | None = None) -> Path | None:
    """Run a system dialog; the chosen path, or None when cancelled. Raises DesktopFileError when
    this system has no such dialog program (zenity is not installed on every Linux desktop)."""
    try:
        result = subprocess.run(  # noqa: S603  Fixed system programs and scripts.
            command,
            capture_output=True,
            check=False,
            env={**os.environ, **(env or {})},
        )
    except OSError as err:
        raise DesktopFileError(command[0]) from err
    chosen = result.stdout.decode("utf-8", "replace").strip()
    return Path(chosen) if result.returncode == 0 and chosen else None


def choose_folder(prompt: str) -> Path | None:
    """Let the user choose a folder in the system's own dialog (the page cannot learn a folder's
    path). None when cancelled."""
    if sys.platform == "darwin":
        script = f"POSIX path of (choose folder with prompt {_applescript_text(prompt)})"
        return _ask(["osascript", "-e", script])
    if sys.platform == "win32":
        command = ["powershell", "-NoProfile", "-STA", "-Command", POWERSHELL_FOLDER]
        return _ask(command, {"JACARA_PROMPT": prompt})
    return _ask(["zenity", "--file-selection", "--directory", f"--title={prompt}"])


def choose_file(prompt: str, extensions: tuple[str, ...]) -> Path | None:
    """Let the user choose a file with one of these extensions (without dots). None when
    cancelled. The caller checks the extension of the chosen file.

    On macOS the dialog lists every file: `choose file of type` takes type identifiers, and an
    extension that no application has registered (.gguf) matches no type, so every file would be
    greyed out."""
    if sys.platform == "darwin":
        script = f"POSIX path of (choose file with prompt {_applescript_text(prompt)})"
        return _ask(["osascript", "-e", script])
    if sys.platform == "win32":
        patterns = ";".join(f"*.{e}" for e in extensions)
        command = ["powershell", "-NoProfile", "-STA", "-Command", POWERSHELL_FILE]
        return _ask(command, {"JACARA_PROMPT": prompt, "JACARA_FILTER": f"{prompt}|{patterns}"})
    patterns = " ".join(f"*.{e}" for e in extensions)
    return _ask(["zenity", "--file-selection", f"--title={prompt}", f"--file-filter={patterns}"])


def open_url(url: str) -> None:
    """Open a web address in the default browser. Only http and https, so a page cannot use this to
    start programs or open local files."""
    import webbrowser

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise DesktopFileError(url)
    webbrowser.open(url)


class SaveToDownloads:
    """ASGI middleware: when a request carries X-Jacara-Save: downloads and the answer is a file
    (status 200, Content-Disposition attachment), the file is written to the Downloads folder as it
    streams and the answer becomes {"name": ..., "path": ...}. Every other request and answer passes
    through unchanged. Enabled only in the desktop app."""

    def __init__(self, app: ASGIApp, enabled: bool, folder: Path | None = None) -> None:
        self.app = app
        self.enabled = enabled
        self.folder = folder

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            not self.enabled
            or scope["type"] != "http"
            or Headers(scope=scope).get(SAVE_HEADER) != "downloads"
        ):
            await self.app(scope, receive, send)
            return
        saving: Saving | None = None
        passing = False

        async def capture(message: Message) -> None:
            nonlocal saving, passing
            if message["type"] == "http.response.start":
                headers = Headers(raw=message.get("headers", []))
                disposition = headers.get("content-disposition", "")
                if message["status"] == 200 and disposition.lower().startswith("attachment"):
                    name = filename_of(disposition) or "download"
                    saving = await anyio.to_thread.run_sync(Saving, name, self.folder)
                    return
                passing = True
                await send(message)
                return
            if message["type"] != "http.response.body" or passing or saving is None:
                await send(message)
                return
            current = saving
            body = message.get("body", b"")
            if body:
                await anyio.to_thread.run_sync(current.write, body)
            if not message.get("more_body", False):
                target = await anyio.to_thread.run_sync(current.finish)
                saving = None
                answer = json.dumps({"name": target.name, "path": str(target)}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(answer)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": answer})

        try:
            await self.app(scope, receive, capture)
        finally:
            if saving is not None:
                # The response stopped half-way (client gone, error): leave no partial file.
                await anyio.to_thread.run_sync(saving.abandon)
