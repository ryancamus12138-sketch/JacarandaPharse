"""Download the llama.cpp builds listed in llama.lock for this platform, check them and keep only
what llama-server needs.

    uv run --group desktop python packaging/desktop/fetch_llama.py [--platform macos-arm64]

Writes build/desktop/llama/<variant>/ (llama-server and its libraries, plus the licence files of
the archive), which the PyInstaller spec ships as llama/<variant>/ inside the package. Downloads
are kept in build/desktop/cache/ and checked again on every run; a file whose size or SHA-256
differs from llama.lock stops the build.

What is kept from each archive:
- macOS: llama-server and the libraries it loads, followed through the @rpath load commands
  (Mach-O LC_LOAD_DYLIB, read with macholib), each under the name it is loaded by. The archive's
  version links (libllama.1.dylib -> libllama.1.0.0.dylib) are resolved, so no links are shipped.
- Windows: llama-server.exe and every DLL except the libraries of the other tools
  (llama-cli-impl.dll and the like); ggml loads its CPU and GPU backends (ggml-cpu-*.dll,
  ggml-vulkan.dll) by name at run time, so all of them stay.
- Linux (packaging checks only): llama-server and every shared library, links resolved."""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LOCK = HERE / "llama.lock"
DOWNLOAD = "https://github.com/ggml-org/llama.cpp/releases/download/{release}/{file}"
TIMEOUT_S = 60
CHUNK = 1 << 20


def fail(message: str) -> None:
    raise SystemExit(f"fetch_llama: {message}")


def this_platform() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    return f"{name}-{arch}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, target: Path, size: int, expected: str) -> None:
    """Download once into the cache; a cached file is checked like a new one."""
    if not url.startswith("https://"):
        fail(f"only https addresses: {url}")
    if not (target.is_file() and target.stat().st_size == size and sha256(target) == expected):
        partial = target.with_name(target.name + ".part")
        print(f"downloading {url}", flush=True)
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response, partial.open("wb") as out:  # noqa: S310  https only, checked above.
            shutil.copyfileobj(response, out, CHUNK)
        partial.replace(target)
    if target.stat().st_size != size:
        fail(f"{target.name}: {target.stat().st_size} bytes, llama.lock says {size}")
    if (actual := sha256(target)) != expected:
        fail(f"{target.name}: SHA-256 {actual}, llama.lock says {expected}")


def extract(archive: Path, into: Path) -> Path:
    """Unpack and return the folder holding llama-server."""
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)  # noqa: S202  Checked against its published SHA-256.
    else:
        with tarfile.open(archive) as t:
            t.extractall(into, filter="data")
    found = [
        p.parent
        for p in into.rglob("llama-server*")
        if p.name in ("llama-server", "llama-server.exe")
    ]
    if len(found) != 1:
        fail(f"{archive.name}: expected one llama-server, found {len(found)}")
    return found[0]


def macos_files(folder: Path) -> dict[str, Path]:
    """llama-server and, following @rpath load commands, every library it loads."""
    from macholib.mach_o import LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB
    from macholib.MachO import MachO

    keep: dict[str, Path] = {}
    pending = ["llama-server"]
    while pending:
        name = pending.pop()
        if name in keep:
            continue
        path = folder / name
        if not path.exists():
            fail(f"{name} is loaded by llama-server but is not in the archive")
        keep[name] = path
        for header in MachO(str(path.resolve())).headers:
            for command, _info, data in header.commands:
                if command.cmd in (LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB) and isinstance(data, bytes):
                    loaded = data.rstrip(b"\0").decode()
                    if loaded.startswith("@rpath/"):
                        pending.append(loaded.removeprefix("@rpath/"))
    return keep


def windows_files(folder: Path) -> dict[str, Path]:
    keep = {"llama-server.exe": folder / "llama-server.exe"}
    for dll in sorted(folder.glob("*.dll")):
        if dll.name.endswith("-impl.dll") and dll.name != "llama-server-impl.dll":
            continue  # The libraries of llama-cli, llama-bench and the other tools.
        keep[dll.name] = dll
    return keep


def linux_files(folder: Path) -> dict[str, Path]:
    keep = {"llama-server": folder / "llama-server"}
    for library in sorted(folder.glob("*.so*")):
        keep[library.name] = library
    return keep


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--platform", default=this_platform())
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "desktop" / "llama")
    parser.add_argument("--cache", type=Path, default=ROOT / "build" / "desktop" / "cache")
    args = parser.parse_args()
    lock = tomllib.loads(LOCK.read_text("utf-8"))
    if lock.get("version") != 1:
        fail("llama.lock: unknown version")
    builds = [b for b in lock["builds"] if b["platform"] == args.platform]
    if not builds:
        fail(f"llama.lock has no build for {args.platform}")
    args.cache.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        shutil.rmtree(args.out)
    for build in builds:
        archive = args.cache / build["file"]
        url = DOWNLOAD.format(release=lock["release"], file=build["file"])
        download(url, archive, build["size"], build["sha256"])
        with tempfile.TemporaryDirectory() as tmp:
            folder = extract(archive, Path(tmp))
            pick = {"macos": macos_files, "windows": windows_files}.get(
                args.platform.split("-")[0], linux_files
            )
            files = pick(folder)
            licences = {p.name: p for p in folder.glob("LICENSE*") if p.is_file()}
            target = args.out / build["variant"]
            target.mkdir(parents=True)
            for name, source in {**files, **licences}.items():
                shutil.copyfile(source.resolve(), target / name)  # Links resolved.
                shutil.copymode(source.resolve(), target / name)
        size = sum(p.stat().st_size for p in target.iterdir())
        print(f"{build['variant']}: {len(files)} files, {size / 1e6:.1f} MB -> {target}")
    (args.out / "RELEASE").write_text(f"{lock['release']} ({lock['upstream_version']})\n", "utf-8")


if __name__ == "__main__":
    main()
