"""Build opencv-python-headless from source without FFmpeg, for the macOS package.

    uv run --group desktop python packaging/desktop/build_opencv.py --install

The macOS wheels on PyPI link FFmpeg built with --enable-gpl (see opencv.lock): importing cv2 loads
libx264 and libx265, which are GPL-2.0-or-later. The app reads images and PDFs only, so this build
leaves FFmpeg out and is otherwise the published build: opencv-python at the locked commit with its
submodules, built as its README's "Manual builds" section describes (ENABLE_HEADLESS=1, extra
CMake arguments in CMAKE_ARGS), here with `uv build --wheel` (PEP 517, the same as `pip wheel .`).

Writes build/desktop/wheels/<hash of opencv.lock>/opencv_python_headless-<version>-*.whl. A wheel
that is already there was built from the same opencv.lock and is reused, so CI can keep the folder
in its cache. With --install the wheel replaces the PyPI wheel
in the project environment; later steps must run with `uv run --no-sync` (or UV_NO_SYNC=1), or uv
puts the PyPI wheel back.

Checks: the version is the one in uv.lock, the clone is at the locked commit, the installed cv2
does not report FFmpeg under Video I/O in cv2.getBuildInformation(), and its binaries link only
system libraries (macOS: /System/Library and /usr/lib; Linux, for checking the script: the C and C++
runtime), so nothing from the build machine ends up in the package."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / "build" / "desktop"
LOCK = HERE / "opencv.lock"

SYSTEM_PREFIXES = ("/System/Library/", "/usr/lib/")
"""macOS: libraries every Mac has."""
LINUX_RUNTIME = re.compile(
    r"^(linux-vdso|ld-linux[-\w]*|libc|libm|libdl|libpthread|librt|libgcc_s|libstdc\+\+)\.so"
)


def fail(message: str) -> None:
    raise SystemExit(f"build_opencv: {message}")


def run(*command: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    print("+", " ".join(command), flush=True)
    done = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, check=False)  # noqa: S603
    if done.returncode != 0:
        fail(f"{' '.join(command)} failed:\n{(done.stderr or done.stdout).strip()[-4000:]}")
    return done.stdout


def locked_version(package: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))
    for entry in lock["package"]:
        if entry["name"] == package:
            return entry["version"]
    fail(f"{package} is not in uv.lock")
    return ""


def wheels_folder() -> Path:
    return BUILD / "wheels" / hashlib.sha256(LOCK.read_bytes()).hexdigest()[:12]


def build(spec: dict) -> Path:
    name = spec["package"].replace("-", "_")
    wheels = wheels_folder()
    found = sorted(wheels.glob(f"{name}-{spec['package_version']}-*.whl"))
    if found:
        print(f"reusing {found[-1].relative_to(ROOT)}")
        return found[-1]
    source = BUILD / "opencv-python"
    shutil.rmtree(source, ignore_errors=True)
    run(
        "git", "clone", "--branch", spec["tag"], "--depth", "1", "--recurse-submodules",
        "--shallow-submodules", spec["repository"], str(source),
    )  # fmt: skip
    head = run("git", "rev-parse", "HEAD", cwd=source).strip()
    if head != spec["commit"]:
        fail(f"tag {spec['tag']} is at {head}, opencv.lock says {spec['commit']}")
    env = dict(os.environ)
    env.update(ENABLE_HEADLESS="1", CMAKE_ARGS=" ".join(spec["cmake_args"]))
    env.pop("ENABLE_CONTRIB", None)
    wheels.mkdir(parents=True, exist_ok=True)
    run("uv", "build", "--wheel", "--out-dir", str(wheels), str(source), env=env)
    found = sorted(wheels.glob(f"{name}-{spec['package_version']}-*.whl"))
    if not found:
        built = ", ".join(p.name for p in wheels.glob(f"{name}-*.whl")) or "nothing"
        fail(f"no wheel for {spec['package_version']} was built (found: {built})")
    shutil.rmtree(source, ignore_errors=True)
    return found[-1]


def install(wheel: Path) -> None:
    run("uv", "pip", "install", "--python", sys.executable, "--no-deps", "--reinstall", str(wheel))
    info = run(sys.executable, "-c", "import cv2; print(cv2.getBuildInformation())")
    # OpenCV lists FFmpeg under "Video I/O" as "FFMPEG: YES (...)" when built with it and leaves the
    # line out (or says NO) when it is not.
    if re.search(r"^\s*FFMPEG:\s+YES", info, re.MULTILINE):
        fail("the installed cv2 was built with FFmpeg")
    check_linking()
    print(f"installed {wheel.name} (without FFmpeg, linking only system libraries)")


def linked(binary: Path) -> list[str]:
    if sys.platform == "darwin":
        from macholib.mach_o import LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB
        from macholib.MachO import MachO

        return [
            data.rstrip(b"\0").decode()
            for header in MachO(str(binary)).headers
            for command, _info, data in header.commands
            if command.cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB) and isinstance(data, bytes)
        ]
    needed = run("readelf", "--dynamic", str(binary))
    return re.findall(r"\(NEEDED\)\s+Shared library: \[(.+?)\]", needed)


def check_linking() -> None:
    if sys.platform not in ("darwin", "linux"):
        return
    package = Path(
        run(sys.executable, "-c", "import cv2, os; print(os.path.dirname(cv2.__file__))").strip()
    )
    binaries = [p for p in package.rglob("*") if p.suffix in (".so", ".dylib") and p.is_file()]
    if not binaries:
        fail(f"no compiled module found in {package}")
    for binary in binaries:
        for library in linked(binary):
            system = (
                library.startswith(SYSTEM_PREFIXES)
                if sys.platform == "darwin"
                else bool(LINUX_RUNTIME.match(library))
            )
            if not system:
                fail(f"{binary.name} links {library}, which is not a system library")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--install", action="store_true", help="install into this environment")
    args = parser.parse_args()
    spec = tomllib.loads(LOCK.read_text("utf-8"))
    if spec.get("version") != 1:
        fail("unknown opencv.lock format")
    wanted = locked_version(spec["package"])
    if wanted != spec["package_version"]:
        fail(f"opencv.lock builds {spec['package_version']}, uv.lock has {wanted}")
    wheel = build(spec)
    if args.install:
        install(wheel)


if __name__ == "__main__":
    main()
