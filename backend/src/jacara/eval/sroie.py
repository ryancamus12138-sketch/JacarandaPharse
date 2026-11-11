"""SROIE 数据集：马来西亚的英文收据，每张有标准答案（商家、日期、地址、总额）。

来源：ICDAR 2019 SROIE 比赛，这里用 GitHub 仓库 zzzDavid/ICDAR-2019-SROIE 整理过的版本（626 张）。
没有写明授权，只用于内部评测，不进仓库、不随安装包分发。

放在项目根目录的 datasets/sroie/ 下（datasets/ 在 .gitignore 里）：
    datasets/sroie/img/000.jpg     收据图片
    datasets/sroie/key/000.json    标准答案 {"company", "date", "address", "total"}
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_URL = "https://github.com/zzzDavid/ICDAR-2019-SROIE.git"

FIELD_MAP = {"merchant": "company", "date": "date", "total": "total"}
"""我们的字段名 → SROIE 的字段名。SROIE 没有单号，地址不在常用 4 个字段里，所以只评这三个。"""


@dataclass(frozen=True)
class Sample:
    """一张评测样本：图片和标准答案（已经换成我们的字段名）。"""

    sample_id: str
    image: Path
    answers: dict[str, str]


def load_sroie(root: Path, limit: int | None = None) -> list[Sample]:
    """读取样本，按编号排序。limit 只取前 N 张，先用少量样本试跑。"""
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
    """从 GitHub 只下载图片和答案两个文件夹，返回样本数。要联网、要装 git，只在开发时用一次。"""
    if (dest / "key").is_dir():
        return len(list((dest / "key").glob("*.json")))
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("要先安装 git 才能下载 SROIE")

    def run(*args: str, cwd: Path | None = None) -> None:
        subprocess.run([git, *args], check=True, cwd=cwd)  # noqa: S603  参数都是写死的

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
