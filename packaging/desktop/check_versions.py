"""Check that every file that states the program's version states the same one.

    python packaging/desktop/check_versions.py [--tag v1.0.0]

pyproject.toml is the source; web/package.json, desktop/package.json, desktop/src-tauri/Cargo.toml
and desktop/src-tauri/tauri.conf.json must match it. tauri.conf.json's version is what the
installers and the macOS bundle carry, and the backend reports pyproject's (importlib.metadata), so
a mismatch would show two versions for one release. With --tag, the tag must be v<version>
(a pre-release tag such as v1.1.0-beta.1 must start with it)."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--tag", help="release tag to check against the version")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    found = {
        "web/package.json": json.loads((ROOT / "web/package.json").read_text("utf-8"))["version"],
        "desktop/package.json": json.loads((ROOT / "desktop/package.json").read_text("utf-8"))[
            "version"
        ],
        "desktop/src-tauri/Cargo.toml": tomllib.loads(
            (ROOT / "desktop/src-tauri/Cargo.toml").read_text("utf-8")
        )["package"]["version"],
        "desktop/src-tauri/tauri.conf.json": json.loads(
            (ROOT / "desktop/src-tauri/tauri.conf.json").read_text("utf-8")
        )["version"],
    }
    wrong = [f"{path}: {v}" for path, v in found.items() if v != version]
    if args.tag and args.tag.split("-")[0] != f"v{version}":
        wrong.append(f"tag {args.tag}")
    if wrong:
        raise SystemExit(f"check_versions: pyproject.toml says {version}, but " + "; ".join(wrong))
    print(f"version {version}: all files agree")


if __name__ == "__main__":
    main()
