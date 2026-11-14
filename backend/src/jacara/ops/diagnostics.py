"""Diagnostic information for a problem report (Settings → About and diagnostics → Copy diagnostic
information), as Obsidian's "Show debug info" offers to copy for bug reports.

Plain text, one item per line, keys in English so that it reads the same whatever the interface
language. It holds versions, the system, the models in use, the background processes and the
service checks. It holds nothing from documents (no file names, no recognized text) and writes the
home folder as ~."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jacara.core.config import Settings
    from jacara.modules.system.schemas import SystemInfo

VERSION_TIMEOUT_S = 5
"""llama-server --version prints its build and exits at once; this only guards a broken binary."""


def report(info: SystemInfo, settings: Settings) -> str:
    from jacara.core.config import data_home
    from jacara.core.i18n import language

    frozen = getattr(sys, "frozen", False)
    kind = ["personal" if info.mode == "personal" else "team"]
    if settings.jacara_desktop:
        kind.append("desktop app")
    kind.append("installed build" if frozen else "from source")
    lines = [
        f"JacarandaParse {info.version} ({', '.join(kind)})",
        f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"Interface language: {language()}",
        "",
        "[System]",
        f"OS: {_os_name()}",
        f"CPU: {platform.machine()}, {os.cpu_count() or '?'} logical cores",
        f"Memory: {_gigabytes(_memory())}",
        f"Python: {platform.python_version()} ({platform.python_implementation()})",
        f"Data folder: {data_home()} ({_gigabytes(_free(data_home()))} free)",
    ]
    if info.desktop is not None:
        lines.append(f"Logs: {info.desktop.log_dir}")
    lines += ["", "[Models]", *_models(info, settings)]
    if info.desktop is not None:
        lines += ["", "[Background processes]"]
        for p in info.desktop.processes:
            note = f" — {p.note}" if p.note and p.state != "running" else ""
            lines.append(f"{p.name}: {p.state}, started {p.starts} time(s){note}")
        if not info.desktop.processes:
            lines.append("not running")
    lines += ["", "[Checks]"]
    lines += [f"{c.name}: {'OK' if c.ok else 'FAILED'} — {c.detail}" for c in info.services]
    folders = settings.watch_folders()
    missing = sum(1 for f in folders if not f.path.expanduser().is_dir())
    lines += [
        "",
        "[Data]",
        f"Documents: {info.data.documents}, pages: {info.data.pages}",
        f"Watch folders: {len(folders)}" + (f" ({missing} not found)" if missing else ""),
    ]
    return _tidy("\n".join(lines)) + "\n"


def _models(info: SystemInfo, settings: Settings) -> list[str]:
    from jacara.modules.model_admin.service import default_label

    model = settings.find_model()
    mmproj = settings.find_mmproj()
    ocr = settings.ocr_models()
    table = settings.table_model()
    lines = [
        f"Language model: {_file(model)}",
        f"Loaded by the model server: {info.model.loaded or 'not reachable'}"
        + (f", context {info.model.context}" if info.model.context else ""),
        f"Vision model: {_file(mmproj)}; reads images: {'yes' if info.model.sees_images else 'no'}"
        f" (mode {info.model.vision_mode})",
        f"Model server: {llama_server_version(settings)}",
        f"OCR: {info.ocr.engine} {info.ocr.version or ''}".rstrip(),
        f"OCR detection: {_file(ocr.get('det')) if 'det' in ocr else default_label('ocr_det')}",
        f"OCR recognition: {_file(ocr.get('rec')) if 'rec' in ocr else default_label('ocr_rec')}",
        f"OCR dictionary: {_file(ocr.get('dict')) if 'dict' in ocr else default_label('ocr_dict')}",
        f"Layout: {info.structure.layout if info.structure else None}",
        f"Table structure: {_file(table)}",
    ]
    return lines


def llama_server_version(settings: Settings) -> str:
    """Path and build of llama-server, from its own --version output."""
    from jacara.plugins.model_backends.server import llama_server_path

    path = llama_server_path(settings)
    if path is None:
        return "llama-server not found"
    try:
        done = subprocess.run(  # noqa: S603  The program's own llama-server.
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"{path} (cannot run: {err})"
    output = (done.stdout + done.stderr).splitlines()
    build = next((line.strip() for line in output if line.startswith("version")), "")
    return f"{path} ({build or 'version unknown'})"


def _file(path: Path | None) -> str:
    """A model file: where it is (relative to the data folder when inside it) and its size."""
    from jacara.core.config import data_home

    if path is None:
        return "none"
    try:
        shown = path.relative_to(data_home()).as_posix()
    except ValueError:
        shown = str(path)
    try:
        size = path.stat().st_size
    except OSError:
        return f"{shown} (missing)"
    return f"{shown} ({size / 1e9:.2f} GB)" if size >= 1e8 else f"{shown} ({size / 1e6:.1f} MB)"


def _os_name() -> str:
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    if sys.platform == "win32":
        release, build, *_ = platform.win32_ver()
        return f"Windows {release} (build {build}, {platform.machine()})"
    return platform.platform()


def _memory() -> int | None:
    """Installed memory in bytes: sysconf on macOS and Linux, GlobalMemoryStatusEx on Windows."""
    if sys.platform == "win32":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [  # ctypes structure layout (MEMORYSTATUSEX).
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return int(status.ullTotalPhys)
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def _free(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _gigabytes(size: int | None) -> str:
    return "unknown" if size is None else f"{size / 1e9:.1f} GB"


def _tidy(text: str) -> str:
    """The home folder as ~ (it holds the user name), and the rest of such a path with /, so a
    report reads the same on every system. Windows paths ignore case and may be written with \\
    or /, so the home folder is matched in both forms and in any case."""
    home = Path.home()
    forms = {str(home).rstrip("\\/"), home.as_posix().rstrip("/")} - {""}
    for form in sorted(forms, key=len, reverse=True):
        pattern = re.escape(form) + r"(?=[\\/]|\b|$)([\\/][^\s'\"()]*)?"
        flags = re.IGNORECASE if sys.platform == "win32" else 0
        text = re.sub(pattern, lambda m: "~" + (m[1] or "").replace("\\", "/"), text, flags=flags)
    return text
