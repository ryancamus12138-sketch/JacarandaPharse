"""SROIE dataset: English-language receipts from Malaysia, each with ground truth (company, date,
address, total).

Source: the ICDAR 2019 SROIE competition, using the curated version in the GitHub repository
zzzDavid/ICDAR-2019-SROIE (626 receipts). No license is stated, so it is used for internal
evaluation only and is neither tracked in the repository nor shipped in the release package.

Location: datasets/sroie/ in the project root (datasets/ is git-ignored):
    datasets/sroie/img/000.jpg     receipt image
    datasets/sroie/key/000.json    ground truth {"company", "date", "address", "total"}"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/zzzDavid/ICDAR-2019-SROIE.git"

FIELD_MAP = {"merchant": "company", "date": "date", "total": "total"}
"""Our field names → SROIE field names. SROIE has no document number and the address is not among
the common fields, so only these three are evaluated."""


@dataclass(frozen=True)
class Sample:
    """One evaluation sample: image and ground truth (already mapped to our field names)."""

    sample_id: str
    image: Path
    answers: dict[str, str]


def load_sroie(root: Path, limit: int | None = None) -> list[Sample]:
    """Load samples sorted by id. limit takes only the first N, for quick trial runs."""
    key_dir, img_dir = root / "key", root / "img"
    if not key_dir.is_dir():
        raise FileNotFoundError(f"找不到 {key_dir}。先运行：uv run jacara eval download")

    samples: list[Sample] = []
    for key_path in sorted(key_dir.glob("*.json")):
        image = img_dir / f"{key_path.stem}.jpg"
        if not image.exists():
            continue
        raw = json.loads(key_path.read_text(encoding="utf-8"))
        answers = {ours: str(raw.get(theirs, "")).strip() for ours, theirs in FIELD_MAP.items()}
        samples.append(Sample(key_path.stem, image, answers))
        if limit is not None and len(samples) >= limit:
            break
    return samples


def download_sroie(dest: Path) -> int:
    """Download only the image and answer directories from GitHub; return the number of samples.
    Requires network access and git; development only, run once."""
    if (dest / "key").is_dir():
        return len(list((dest / "key").glob("*.json")))
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("要先安装 git 才能下载 SROIE")

    def run(*args: str, cwd: Path | None = None) -> None:
        subprocess.run([git, *args], check=True, cwd=cwd)  # noqa: S603  All arguments are fixed.

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "sroie"
        run(
            "clone",
            "-q",
            "--depth",
            "1",
            "--filter=blob:none",
            "--no-checkout",
            REPO_URL,
            str(repo),
        )
        run("sparse-checkout", "set", "--no-cone", "data/img/*", "data/key/*", cwd=repo)
        run("checkout", "-q", cwd=repo)
        dest.mkdir(parents=True, exist_ok=True)
        for sub in ("img", "key"):
            shutil.copytree(repo / "data" / sub, dest / sub, dirs_exist_ok=True)
    return len(list((dest / "key").glob("*.json")))
