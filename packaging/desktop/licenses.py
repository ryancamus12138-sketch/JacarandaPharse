"""Make THIRD_PARTY_LICENSES: the licences of everything the desktop app ships, and stop the build
when a GPL, LGPL or AGPL licence turns up.

    uv run --group desktop python packaging/desktop/licenses.py [--platform macos-arm64]

Run from the project root after `uv sync --group desktop`, `npm ci` in web/ and fetch_llama.py.
Writes build/desktop/THIRD_PARTY_LICENSES, which the PyInstaller spec ships and Settings → About
and diagnostics shows. Sources:
- Python packages that ship: the runtime dependency tree from `uv tree`, without the packages the
  desktop app leaves out (jacara.spec excludes Celery, Valkey, S3 and PostgreSQL clients); the
  licence name from pip-licenses (MIT), and every licence and notice file the package ships: those
  its metadata lists (License-File, PEP 639), those in its .dist-info and those at the top of its
  import package (onnxruntime's ThirdPartyNotices.txt, for example). Packages that bundle native
  libraries list them there (opencv-python-headless: LICENSE-3RD-PARTY.txt, with FFmpeg's LGPL;
  shapely: LICENSE_GEOS);
- web packages: license-checker-rseidelsohn (BSD-3-Clause) with --production;
- the Rust crates of the Tauri shell: cargo-about (MIT OR Apache-2.0), `generate --format json`,
  configured by desktop/src-tauri/about.toml (shipped targets, accepted licences);
- by hand: llama.cpp (MIT) and, on Windows, the LLVM OpenMP runtime it ships; the Python runtime
  (PSF); the PyInstaller bootloader (GPL-2.0-or-later with the bootloader exception, which allows
  any licence for the program); the PaddleOCR models (Apache-2.0); and, not shipped but named,
  the Qwen3.5-9B model the app offers to download.

A GPL, LGPL or AGPL licence on any Python, web or Rust package stops the build (the project is
Apache-2.0 and takes no GPL-family dependency). For a Rust crate offered under several licences,
the check applies to the one cargo-about chose. The PyInstaller bootloader is the one exception and
is named here on purpose (its exception is what makes it usable)."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "build" / "desktop" / "THIRD_PARTY_LICENSES"

LEFT_OUT = ("celery", "kombu", "redis", "boto3", "botocore", "psycopg", "psycopg-binary", "scipy")
"""Not in the desktop package (jacara.spec excludes them); pruned with what only they need. scipy is
used only by the evaluation commands, which are development tools."""

DECLARED_BY_HAND = {"huey": "MIT"}
"""Packages whose metadata names no licence (no License-Expression, License or classifier); the
licence here was read from the LICENSE file the package ships, which is included below it."""

BUNDLED_ALLOWED = {
    ("GCC runtime library", "GPL-3.0-or-later WITH GCC-exception-3.1"): (
        "The GCC Runtime Library Exception 3.1 allows code compiled by GCC and combined with the "
        "runtime library to be conveyed under terms of the distributor's choice."
    ),
    ("libquadmath", "LGPL-2.1-or-later"): (
        "A separate shared library, loaded at run time and replaceable (LGPL-2.1 section 6(b))."
    ),
}
"""GPL-family components that a package declares as bundled in its licence file ("Name:", "Files:",
"License:" blocks, the format NumPy's wheels use for the OpenBLAS build they ship), with why each
is allowed. Any other GPL-family component stops the build."""

BUNDLED_BLOCK = re.compile(
    r"^Name: (?P<name>.+?)\n(?:.*\n)*?License: (?P<licence>.+)$", re.MULTILINE
)

FORBIDDEN = re.compile(r"\b(A|L)?GPL\b|General Public License", re.IGNORECASE)
"""GPL, LGPL and AGPL, by abbreviation or name ("GNU Lesser General Public License")."""

RULE = "=" * 78
LINE = "-" * 78


def fail(message: str) -> None:
    raise SystemExit(f"licenses: {message}")


def run(*command: str, cwd: Path = ROOT) -> str:
    # The program is looked up first: on Windows npx is npx.cmd, which CreateProcess does not find
    # by its bare name. The tools write UTF-8 whatever the system's code page.
    program = shutil.which(command[0])
    if program is None:
        fail(f"{command[0]} was not found")
        return ""
    done = subprocess.run(  # noqa: S603
        [program, *command[1:]],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if done.returncode != 0:
        fail(f"{' '.join(command)} failed:\n{done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def section(title: str) -> list[str]:
    return ["", RULE, title, RULE]


def entry(name: str, version: str, licence: str, *texts: str) -> list[str]:
    lines = ["", LINE, f"{name} {version}".rstrip(), f"License: {licence}", LINE]
    for text in texts:
        if text and text.strip() and text.strip().upper() != "UNKNOWN":
            lines += ["", text.strip()]
    return lines


def forbidden(where: str, name: str, licence: str) -> None:
    if FORBIDDEN.search(licence):
        fail(f"{where} {name} is licensed {licence!r}: GPL, LGPL and AGPL are not allowed")


def bundled_components(package: str, text: str) -> list[str]:
    """Check the components a package's licence file declares as bundled; notes for the allowed
    GPL-family ones."""
    notes = []
    for block in BUNDLED_BLOCK.finditer(text):
        name, licence = block["name"].strip(), block["licence"].strip()
        if not FORBIDDEN.search(licence):
            continue
        reason = BUNDLED_ALLOWED.get((name, licence))
        if reason is None:
            fail(f"{package} bundles {name} under {licence!r}, which is not on the allowed list")
        notes.append(f"Bundled {name} ({licence}): {reason}")
    return notes


# ---- Python ----

LICENCE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|THIRDPARTYNOTICES|THIRD[-_]PARTY)", re.I)


def licence_files(name: str) -> list[str]:
    """Texts of the licence and notice files a package ships, each headed by its path."""
    dist = distribution(name)
    listed = {f"licenses/{f}" for f in dist.metadata.get_all("License-File") or []}
    texts: dict[str, str] = {}
    for file in dist.files or []:
        path = PurePosixPath(str(file))
        in_metadata = path.parts[0].endswith(".dist-info")
        relative = "/".join(path.parts[1:])
        wanted = (in_metadata and (relative in listed or LICENCE_FILE.match(path.name))) or (
            not in_metadata and len(path.parts) == 2 and LICENCE_FILE.match(path.name)
        )
        if not wanted or path.suffix in (".py", ".pyc"):
            continue
        text = Path(str(dist.locate_file(file))).read_text("utf-8", errors="replace").strip()
        if text and text not in texts.values():
            texts[str(path)] = text
    return [f"[{path}]\n\n{text}" for path, text in sorted(texts.items())]


def python_packages() -> list[str]:
    """Runtime dependencies that ship: uv tree without dev groups, without the left-out packages."""
    prune = [arg for name in LEFT_OUT for arg in ("--prune", name)]
    tree = run("uv", "tree", "--frozen", "--no-dev", "--no-dedupe", *prune)
    names: dict[str, None] = {}
    for line in tree.splitlines()[1:]:  # The first line is jacara itself.
        if match := re.search(r"([A-Za-z0-9][A-Za-z0-9._-]*) v\d", line):
            names[match[1].lower()] = None
    return sorted(names)


def python_section() -> list[str]:
    names = python_packages()
    installed = []
    for name in names:
        try:
            distribution(name)
            installed.append(name)
        except PackageNotFoundError:
            pass  # A dependency for another platform only (environment marker).
    data = json.loads(
        run(
            sys.executable, "-m", "piplicenses",
            "--from=mixed", "--format=json", "--with-license-file", "--with-notice-file",
            "--no-license-path", "--packages", *installed,
        )
    )  # fmt: skip
    lines = section(f"Python packages ({len(data)})")
    for package in sorted(data, key=lambda p: p["Name"].lower()):
        name, licence = package["Name"], package["License"]
        texts = licence_files(name)
        if not texts:
            texts = [
                t
                for t in (package.get("LicenseText", ""), package.get("NoticeText", ""))
                if t.strip() and t.strip().upper() != "UNKNOWN"
            ]
        if licence.strip().upper() == "UNKNOWN":
            licence = DECLARED_BY_HAND.get(name.lower(), "")
            if not licence or not texts:
                fail(f"Python package {name} declares no licence and ships no licence file")
        forbidden("Python package", name, licence)
        notes = [n for text in texts for n in bundled_components(name, text)]
        lines += entry(name, package["Version"], licence, "\n".join(notes), *texts)
    return lines


# ---- Web ----


def web_section() -> list[str]:
    data = json.loads(
        run(
            "npx", "--yes", "license-checker-rseidelsohn@5.0.1",
            "--production", "--excludePrivatePackages", "--json", "--start", str(ROOT / "web"),
        )
    )  # fmt: skip
    lines = section(f"Web packages ({len(data)})")
    for key in sorted(data, key=str.lower):
        info = data[key]
        name, _, version = key.rpartition("@")
        licence = info.get("licenses", "UNKNOWN")
        licence = " OR ".join(licence) if isinstance(licence, list) else str(licence)
        forbidden("Web package", name, licence)
        text = ""
        if path := info.get("licenseFile"):
            text = Path(path).read_text("utf-8", errors="replace")
        lines += entry(name, version, licence, text)
    return lines


# ---- Rust ----


def rust_section() -> list[str]:
    shell = ROOT / "desktop" / "src-tauri"
    data = json.loads(run("cargo", "about", "generate", "--format", "json", cwd=shell))
    # "licenses" holds each licence text cargo-about chose, with the crates it applies to; "crates"
    # holds each crate's own licence expression, which may offer several licences.
    crates: dict[str, tuple[str, str, list[str], list[str]]] = {}
    for licence in data.get("licenses", []):
        for used in licence.get("used_by", []):
            crate = used.get("crate", {})
            key = f"{crate.get('name')} {crate.get('version')}"
            _, _, chosen, texts = crates.setdefault(
                key, (str(crate.get("name")), str(crate.get("version")), [], [])
            )
            if licence.get("id") not in chosen:
                chosen.append(str(licence.get("id")))
            texts.append(licence.get("text", ""))
    offered = {
        f"{c['package']['name']} {c['package']['version']}": c.get("license") or ""
        for c in data.get("crates", [])
        if "package" in c
    }
    lines = section(f"Rust crates of the application shell ({len(crates)})")
    lines.append("")
    for key in sorted(crates, key=str.lower):
        name, version, chosen, _ = crates[key]
        licence = " AND ".join(chosen)
        forbidden("Rust crate", name, licence)
        expression = offered.get(key, "")
        if expression and expression != licence:
            licence = f"{licence} (offered: {expression})"
        lines.append(f"{name} {version}: {licence}")
    # Each licence text once, with the crates it comes from (as cargo-about's own template does).
    users: dict[tuple[str, str], list[str]] = {}
    for licence in data.get("licenses", []):
        text = licence.get("text", "").strip()
        names = [
            f"{u['crate']['name']} {u['crate']['version']}" for u in licence.get("used_by", [])
        ]
        users.setdefault((str(licence.get("id")), text), []).extend(names)
    for (licence_id, text), names in sorted(users.items(), key=lambda kv: (kv[0][0], kv[1])):
        lines += entry(
            f"Licence text used by {', '.join(sorted(set(names)))}", "", licence_id, text
        )
    return lines


# ---- By hand ----


def python_runtime() -> str:
    for candidate in (
        Path(sys.base_prefix)
        / "lib"
        / f"python{sys.version_info[0]}.{sys.version_info[1]}"
        / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
    ):
        if candidate.is_file():
            return candidate.read_text("utf-8", errors="replace")
    fail(f"the licence of the Python runtime was not found under {sys.base_prefix}")
    return ""


def pyinstaller_licence() -> tuple[str, str]:
    dist = distribution("pyinstaller")
    for file in dist.files or []:
        if Path(str(file)).name.upper().startswith("COPYING"):
            return dist.version, Path(str(dist.locate_file(file))).read_text("utf-8")
    fail("PyInstaller's COPYING.txt was not found")
    return "", ""


def by_hand() -> list[str]:
    lock = tomllib.loads((HERE / "llama.lock").read_text("utf-8"))
    lines = section("Other software and models")
    lines += entry(
        "llama.cpp",
        f"{lock['upstream_version']} (build {lock['release']})",
        lock["license"],
        (HERE / "licenses" / "llama.cpp.LICENSE").read_text("utf-8"),
    )
    openmp = sorted((ROOT / "build" / "desktop" / "llama").glob("*/LICENSE-LLVM-OpenMP"))
    if openmp:
        lines += entry(
            "LLVM OpenMP runtime (libomp, shipped with llama.cpp for Windows)",
            "",
            "Apache-2.0 WITH LLVM-exception",
            openmp[0].read_text("utf-8", errors="replace"),
        )
    version = f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"
    lines += entry("Python", version, "PSF-2.0", python_runtime())
    pyinstaller_version, pyinstaller_text = pyinstaller_licence()
    lines += entry(
        "PyInstaller bootloader",
        pyinstaller_version,
        "GPL-2.0-or-later WITH Bootloader-exception",
        "The bootloader exception allows the bootloader to be combined with a program under any "
        "licence; the program itself is not covered by the GPL.",
        pyinstaller_text,
    )
    lines += entry(
        "PaddleOCR models: PP-OCRv6_det_small, PP-OCRv6_rec_small (shipped inside RapidOCR), "
        "pp_layout_cdla (shipped inside rapid-layout), SLANet-plus (shipped with the program)",
        "",
        "Apache-2.0",
        "Models of the PaddleOCR project (https://github.com/PaddlePaddle/PaddleOCR). The Apache "
        "License 2.0 text is the same as in the LICENSE file of this program.",
    )
    lines += entry(
        "Qwen3.5-9B (not shipped: downloaded at the user's request)",
        "",
        "Apache-2.0",
        "Model by the Qwen team (Qwen/Qwen3.5-9B-Base); GGUF conversion by unsloth "
        "(unsloth/Qwen3.5-9B-GGUF), also Apache-2.0.",
    )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    lines = [
        "JacarandaParse: third-party software",
        f"Licences of the software and models shipped with JacarandaParse {version} "
        f"({sys.platform}), generated {stamp}.",
        "JacarandaParse itself is licensed under the Apache License 2.0 (see LICENSE and NOTICE).",
    ]
    lines += python_section()
    lines += web_section()
    lines += rust_section()
    lines += by_hand()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", "utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
