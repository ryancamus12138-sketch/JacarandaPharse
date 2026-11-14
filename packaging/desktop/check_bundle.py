"""Check the packaged backend before it goes into the installer.

    python packaging/desktop/check_bundle.py dist/jacara-backend [--sign]

Fails (exit 1) when:
- a file the app needs is missing: the program, LICENSE, NOTICE, THIRD_PARTY_LICENSES, a
  llama-server build, the table model;
- a model file is not one of the known ones: only RapidOCR's own models, rapid-layout's
  layout_cdla.onnx and SLANet-plus may ship. rapid-layout's YOLOv8 weights are AGPL, and no model
  the user downloads (.gguf) may end up in the installer;
- a GPL library is in it: x264, x265 and the other libraries only a GPL build of FFmpeg uses, or an
  FFmpeg library whose embedded configuration has --enable-gpl or --enable-nonfree, or whose
  embedded licence string is GPL rather than LGPL (the PyPI macOS wheels of opencv-python-headless
  have such a build, so the macOS package uses build_opencv.py);
- a package that jacara.spec leaves out is in it anyway (psycopg is LGPL; the others are not used
  by the desktop app);
- tests, docs or Markdown documents are in the package (only package metadata may hold .md
  files): the installer carries the program, not the private repository's material;
- macOS: a binary needs a newer macOS than bundle.macOS.minimumSystemVersion in tauri.conf.json,
  or a binary is not signed. Apple silicon runs only signed code; with --sign each unsigned
  binary is signed ad hoc (`codesign --sign -`, Tauri's documentation for apps without an Apple
  certificate) and checked again with `codesign --verify --strict`."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAURI_CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"

WEIGHTS = (".onnx", ".pt", ".pth", ".safetensors", ".engine", ".gguf", ".ckpt", ".tflite")
ALLOWED_WEIGHTS = (
    "rapidocr/models/*.onnx",
    "rapid_layout/models/layout_cdla.onnx",
    "models/table/slanet-plus.onnx",
)
"""Paths inside the package's data folder (_internal)."""

LEFT_OUT = ("celery", "kombu", "redis", "boto3", "botocore", "psycopg", "psycopg_binary", "scipy")
"""Top-level packages jacara.spec excludes (licenses.py leaves the same ones out of the list)."""

GPL_LIBRARY = re.compile(
    r"^(lib)?(x264|x265|rubberband|vidstab|postproc|xvidcore|frei0r|fdk-aac)\b", re.IGNORECASE
)
"""GPL libraries (and fdk-aac, which FFmpeg may use only as --enable-nonfree) that FFmpeg links
only when configured with --enable-gpl or --enable-nonfree."""

FFMPEG_LIBRARY = re.compile(
    r"(avcodec|avformat|avutil|avfilter|avdevice|swscale|swresample|videoio_ffmpeg)", re.IGNORECASE
)
FFMPEG_GPL = re.compile(
    rb"--enable-gpl|--enable-nonfree|nonfree and unredistributable|(?<![L\w])GPL version \d"
)
"""FFmpeg embeds its configure command line (FFMPEG_CONFIGURATION) and its licence (FFMPEG_LICENSE:
"LGPL version 2.1 or later", "GPL version 2 or later", "nonfree and unredistributable")."""

CODESIGN = "/usr/bin/codesign"

MACHO_MAGIC = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
}

problems: list[str] = []


def problem(text: str) -> None:
    problems.append(text)
    print(f"FAIL  {text}", flush=True)


def data_folder(bundle: Path) -> Path:
    """PyInstaller 6 puts everything but the program into _internal/."""
    internal = bundle / "_internal"
    return internal if internal.is_dir() else bundle


def check_files(bundle: Path) -> None:
    data = data_folder(bundle)
    program = bundle / ("jacara-backend.exe" if sys.platform == "win32" else "jacara-backend")
    for path in (
        program,
        data / "LICENSE",
        data / "NOTICE",
        data / "THIRD_PARTY_LICENSES",
        data / "models" / "table" / "slanet-plus.onnx",
    ):
        if not path.is_file():
            problem(f"missing: {path.relative_to(bundle)}")
    servers = sorted((data / "llama").glob("*/llama-server*"))
    if not servers:
        problem("missing: llama/<variant>/llama-server")
    for server in servers:
        done = subprocess.run(  # noqa: S603
            [str(server), "--version"], capture_output=True, text=True, timeout=60, check=False
        )
        output = done.stdout + done.stderr
        if "version" not in output:
            problem(f"{server.relative_to(bundle)} --version does not run: {output.strip()[:300]}")
        else:
            print(f"ok    {server.relative_to(bundle)}: {output.strip().splitlines()[0]}")


def check_contents(bundle: Path) -> None:
    data = data_folder(bundle)
    for name in LEFT_OUT:
        if (data / name).exists() or any(data.glob(f"{name}.libs")) or any(data.glob(f"{name}-*")):
            problem(f"left-out package in the package: {name}")
    for path in sorted(data.rglob("*")):
        relative = path.relative_to(data).as_posix()
        parts = path.relative_to(data).parts
        if path.is_dir() and path.name in ("tests", "docs"):
            problem(f"{path.name} folder in the package: {relative}")
        if not path.is_file():
            continue
        if path.suffix.lower() in WEIGHTS and not any(
            fnmatch.fnmatchcase(relative, pattern) for pattern in ALLOWED_WEIGHTS
        ):
            problem(f"model file that may not ship: {relative}")
        if path.suffix.lower() == ".md" and not any(p.endswith(".dist-info") for p in parts):
            problem(f"Markdown document in the package: {relative}")
        if GPL_LIBRARY.match(path.name):
            problem(f"GPL library in the package: {relative}")
        elif FFMPEG_LIBRARY.search(path.name) and FFMPEG_GPL.search(path.read_bytes()):
            problem(f"FFmpeg library built as GPL or nonfree: {relative}")


def macho_files(bundle: Path) -> list[Path]:
    found = []
    for path in sorted(bundle.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f:
                if f.read(4) in MACHO_MAGIC:
                    found.append(path)
    return found


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def minimum_macos(path: Path) -> str | None:
    from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX
    from macholib.MachO import MachO

    found: list[str] = []
    for header in MachO(str(path)).headers:
        for command, info, _data in header.commands:
            if command.cmd == LC_BUILD_VERSION:
                v = info.minos
            elif command.cmd == LC_VERSION_MIN_MACOSX:
                v = info.version
            else:
                continue
            found.append(f"{v >> 16}.{(v >> 8) & 255}.{v & 255}")
    return max(found, key=version_tuple) if found else None


def check_macos(bundle: Path, sign: bool) -> None:
    conf = json.loads(TAURI_CONF.read_text("utf-8"))
    allowed = conf["bundle"]["macOS"]["minimumSystemVersion"]
    highest: tuple[str, Path] | None = None
    for path in macho_files(bundle):
        needed = minimum_macos(path)
        if needed and (highest is None or version_tuple(needed) > version_tuple(highest[0])):
            highest = (needed, path)
        verified = subprocess.run(  # noqa: S603
            [CODESIGN, "--verify", "--strict", str(path)], capture_output=True, check=False
        )
        if verified.returncode != 0 and sign:
            subprocess.run(  # noqa: S603
                [CODESIGN, "--force", "--sign", "-", str(path)], check=True, capture_output=True
            )
            verified = subprocess.run(  # noqa: S603
                [CODESIGN, "--verify", "--strict", str(path)], capture_output=True, check=False
            )
        if verified.returncode != 0:
            problem(f"not signed: {path.relative_to(bundle)}")
    if highest is not None:
        print(f"ok    highest minimum macOS: {highest[0]} ({highest[1].relative_to(bundle)})")
        if version_tuple(highest[0]) > version_tuple(allowed):
            problem(
                f"{highest[1].relative_to(bundle)} needs macOS {highest[0]}, "
                f"tauri.conf.json says {allowed}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--sign", action="store_true", help="sign unsigned binaries ad hoc (macOS)")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    check_files(bundle)
    check_contents(bundle)
    if sys.platform == "darwin":
        check_macos(bundle, args.sign)
    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    print(f"package: {size / 1e6:.0f} MB")
    if problems:
        raise SystemExit(f"check_bundle: {len(problems)} problem(s)")
    print("check_bundle: all checks passed")


if __name__ == "__main__":
    main()
