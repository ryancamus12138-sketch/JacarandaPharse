"""The llama.cpp builds shipped with the desktop app, and which one starts.

The package holds llama/<variant>/llama-server[.exe] (packaging/desktop/fetch_llama.py): one build
on macOS (metal), two on Windows (cpu and vulkan). On Windows the setting jacara_llama_device
chooses: cpu, vulkan, or auto (the default), which starts the Vulkan build when that build's
`llama-server --list-devices` ("print list of available devices and exit") reports a GPU, and the
CPU build otherwise. The answer is kept in data/llama-devices.json, so the check runs once per
installed build and not at every start."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jacara.core.config import Settings

EXE = "llama-server.exe" if sys.platform == "win32" else "llama-server"
LIST_TIMEOUT_S = 30
"""--list-devices loads the GPU driver and exits; a broken driver must not hold up the start."""

DEVICE = re.compile(r"^\s*(Vulkan\d+)\s*:\s*(.+?)\s*$")
"""A device line of `llama-server --list-devices`: "  Vulkan0: <description> (<memory>)". Without a
usable GPU the list is "  (none)"."""

DEVICES_FILE = "data/llama-devices.json"


def bundled() -> dict[str, Path]:
    """The builds in this package: variant → llama-server. Empty when running from source."""
    from jacara.core.config import resource_dir

    root = resource_dir() / "llama"
    if not root.is_dir():
        return {}
    return {d.name: d / EXE for d in sorted(root.iterdir()) if (d / EXE).is_file()}


def gpu_devices(exe: Path) -> list[str]:
    """GPUs this llama-server build can use, from its own --list-devices."""
    try:
        done = subprocess.run(  # noqa: S603  The program's own llama-server.
            [str(exe), "--list-devices"],
            capture_output=True,
            text=True,
            timeout=LIST_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = (DEVICE.match(line) for line in (done.stdout + "\n" + done.stderr).splitlines())
    return [f"{m[1]}: {m[2]}" for m in found if m]


def detected(exe: Path) -> list[str]:
    """GPUs of the Vulkan build, checked once per build (size and modification time)."""
    from jacara.core.config import data_home

    stat = exe.stat()
    key = f"{exe.parent.name}:{stat.st_size}:{int(stat.st_mtime)}"
    path = data_home() / DEVICES_FILE
    try:
        saved = json.loads(path.read_text("utf-8"))
        if isinstance(saved, dict) and saved.get("build") == key:
            return [str(d) for d in saved.get("devices", [])]
    except (OSError, ValueError):
        pass
    devices = gpu_devices(exe)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"build": key, "devices": devices}, ensure_ascii=False), "utf-8")
    return devices


def choose(settings: Settings) -> Path | None:
    """The shipped llama-server to start; None when the package has none (running from source)."""
    builds = bundled()
    if not builds:
        return None
    if len(builds) == 1:
        return next(iter(builds.values()))
    wanted = settings.jacara_llama_device
    if wanted in builds:
        return builds[wanted]
    if "vulkan" in builds and detected(builds["vulkan"]):
        return builds["vulkan"]
    return builds.get("cpu") or next(iter(builds.values()))


def device_note() -> str | None:
    """For the settings page: what automatic choice found. None without a choice to make."""
    from jacara.core.i18n import join_items, render, tr

    builds = bundled()
    if "vulkan" not in builds or len(builds) < 2:
        return None
    devices = detected(builds["vulkan"])
    if devices:
        return tr("检测到的显卡：{devices}", devices=render(join_items(devices)))
    return tr("没有检测到可用的显卡（Vulkan），自动选择时使用 CPU。")
