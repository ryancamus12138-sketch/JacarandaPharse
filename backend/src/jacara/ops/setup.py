"""First-time setup: `jacara setup`.

Runs step by step, reporting what each step did and how to fix a failure. Safe to re-run:
completed steps are skipped and an existing .env is left untouched.
1. .env: generated from .env.example if missing, with random secrets, personal edition and API
   docs disabled;
2. Docker: start the database, task queue and object storage (docker compose up -d --wait);
3. Create tables (alembic upgrade head);
4. Check the frontend, llama-server and model files, and explain where to obtain missing ones."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PLACEHOLDER = "change-me"
SECRET_KEYS = ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY")
PERSONAL = {"JACARA_MODE": "personal", "JACARA_API_DOCS": "false"}
"""Defaults for personal releases: personal edition (no login) and API docs disabled."""

MODEL_URL = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
VISION_URL = "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/mmproj-F16.gguf"


@dataclass
class Step:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    required: bool = True
    """False: optional (for example the vision projector); reported as a warning only."""


def make_env(example: str, token: Callable[[int], str] = secrets.token_hex) -> str:
    """Generate .env from .env.example: required secrets replaced by random values, personal-edition
    defaults set, everything else unchanged."""
    lines = []
    for line in example.splitlines():
        key, sep, value = line.partition("=")
        name = key.strip()
        if sep and not line.lstrip().startswith("#"):
            if name in SECRET_KEYS and value.strip() == PLACEHOLDER:
                line = f"{name}={token(16)}"
            elif name in PERSONAL:
                line = f"{name}={PERSONAL[name]}"
        lines.append(line)
    return "\n".join(lines) + "\n"


def ensure_env(root: Path) -> Step:
    from jacara.core.config import get_settings

    env = root / ".env"
    if env.exists():
        get_settings.cache_clear()
        weak = get_settings().weak_secrets()
        if weak:
            return Step(
                ".env",
                False,
                f"已经有了，但 {', '.join(weak)} 还是空的或 change-me",
                "改成随机值（openssl rand -hex 16）。"
                "数据库已经建好的话别改 POSTGRES_PASSWORD，否则连不上",
            )
        return Step(".env", True, "已经有了，不动它")
    example = root / ".env.example"
    if not example.is_file():
        return Step(".env", False, "找不到 .env.example", "从发布包里重新解压一份")
    # Owner read/write only (contains secrets).
    fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(make_env(example.read_text("utf-8")))
    get_settings.cache_clear()
    return Step(".env", True, "生成好了：密钥是随机的，个人版（不用登录），只有你自己能读")


def _run(args: list[str], root: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  Command and arguments are fixed.
        args, cwd=root, capture_output=True, text=True, timeout=timeout, check=False
    )


def docker_up(root: Path) -> Step:
    docker = shutil.which("docker")
    if docker is None:
        return Step(
            "数据库等服务",
            False,
            "没找到 Docker",
            "安装 Docker Desktop（https://www.docker.com/products/docker-desktop/），打开它，再运行一次",
        )
    try:
        if _run([docker, "info"], root, 30).returncode != 0:
            return Step(
                "数据库等服务",
                False,
                "Docker 没在运行",
                "打开 Docker Desktop，等它启动好再运行一次",
            )
        done = _run([docker, "compose", "up", "-d", "--wait"], root, 900)
    except subprocess.TimeoutExpired:
        return Step(
            "数据库等服务",
            False,
            "Docker 太久没有回应",
            "看看 Docker Desktop 是不是卡住了，再运行一次",
        )
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
        return Step(
            "数据库等服务",
            False,
            "docker compose 没启动成功：" + " / ".join(tail),
            "第一次要联网下载镜像；端口 5432、6379、8333 被别的程序占着也会失败",
        )
    return Step("数据库等服务", True, "PostgreSQL、Valkey、SeaweedFS 都在运行（只监听本机）")


def migrate(root: Path) -> Step:
    from alembic import command
    from alembic.config import Config

    try:
        command.upgrade(Config(str(root / "alembic.ini")), "head")
    except Exception as err:  # Report unreachable databases and migration errors alike.
        # Database errors span several lines; collapse them into one.
        reason = " ".join(str(err).split())[:200]
        return Step("建表", False, f"{type(err).__name__}: {reason}", "先确认上一步数据库在运行")
    return Step("建表", True, "数据库是最新的")


def web_ready() -> Step:
    from jacara.core.config import get_settings

    if (get_settings().web_dist / "index.html").is_file():
        return Step("网页", True, "打包好的网页在")
    return Step(
        "网页",
        False,
        "没有打包好的网页（web/dist）",
        "发布包里已经带着；从源码装的话：cd web && npm install && npm run build",
    )


def llama_ready() -> Step:
    if shutil.which("llama-server"):
        return Step("llama-server", True, "找到了")
    return Step(
        "llama-server", False, "没找到运行大模型的 llama-server", "Mac：brew install llama.cpp"
    )


def models_ready() -> list[Step]:
    from jacara.core.config import get_settings

    settings = get_settings()
    steps = []
    model = settings.find_model()
    if model is not None:
        steps.append(Step("大模型", True, model.name))
    else:
        steps.append(
            Step(
                "大模型",
                False,
                "models/ 里还没有大模型（.gguf）",
                f"在浏览器里下载 {MODEL_URL}（约 5.7 GB，Apache-2.0），放进 models/",
            )
        )
    vision = settings.find_mmproj()
    steps.append(
        Step("视觉模型", True, vision.name, required=False)
        if vision
        else Step(
            "视觉模型",
            False,
            "没有（可选：照片、扫描件 OCR 认不准时让模型看原图）",
            f"要的话下载 {VISION_URL}（约 918 MB），放进 models/",
            required=False,
        )
    )
    return steps


def run_setup(root: Path, docker: bool = True, out: Callable[[str], None] = print) -> int:
    """Run the steps in order; steps depending on a failed required step are skipped. Returns 0 when
    `jacara start` can be run."""
    os.chdir(root)  # .env, alembic.ini and docker compose are resolved from the project root.
    steps: list[Step] = []

    def show(step: Step) -> Step:
        mark = "✓" if step.ok else ("✗" if step.required else "·")
        out(f"{mark} {step.name}：{step.detail}")
        if step.hint and not step.ok:
            out(f"    → {step.hint}")
        steps.append(step)
        return step

    out("JacarandaParse 个人版：第一次安装（可以重复运行）\n")
    env = show(ensure_env(root))
    if env.ok and docker:
        if show(docker_up(root)).ok:
            show(migrate(root))
    elif env.ok:
        show(migrate(root))
    show(web_ready())
    show(llama_ready())
    for step in models_ready():
        show(step)
    missing = [s for s in steps if s.required and not s.ok]
    if missing:
        out(f"\n还差 {len(missing)} 步，照上面的 → 做好后再运行一次：")
        out("uv run --frozen --no-dev jacara setup")
        return 1
    out("\n都好了。以后每次用：uv run --frozen --no-dev jacara start")
    out("（Mac 也可以双击文件夹里的 JacarandaParse.command）")
    return 0
