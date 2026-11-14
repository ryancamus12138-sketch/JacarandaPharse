"""First-time setup: .env generation and the result and hints of each step. Docker is not started
and no database is contacted."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from jacara.core.config import get_settings
from jacara.ops import setup
from jacara.ops.setup import Step, ensure_env, make_env, run_setup

EXAMPLE = """# 注释里的 change-me 不动
JACARA_MODE=team
JACARA_API_DOCS=true
POSTGRES_USER=jacara
POSTGRES_PASSWORD=change-me
S3_ACCESS_KEY=change-me
S3_SECRET_KEY=change-me
# JACARA_VISION=auto
"""


def test_make_env_fills_random_secrets_and_personal_defaults():
    numbers = iter(range(100))
    text = make_env(EXAMPLE, lambda n: f"secret{next(numbers)}x{n}")
    lines = text.splitlines()
    assert "POSTGRES_PASSWORD=secret0x16" in lines
    assert "S3_ACCESS_KEY=secret1x16" in lines and "S3_SECRET_KEY=secret2x16" in lines
    assert "JACARA_MODE=personal" in lines and "JACARA_API_DOCS=false" in lines
    assert "# 注释里的 change-me 不动" in lines and "# JACARA_VISION=auto" in lines
    assert "POSTGRES_USER=jacara" in lines


def test_real_example_has_no_placeholder_left():
    root = Path(__file__).resolve().parents[2]
    text = make_env((root / ".env.example").read_text("utf-8"))
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in setup.SECRET_KEYS:
            assert len(value) == 32 and value != "change-me"


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text(EXAMPLE, "utf-8")
    monkeypatch.chdir(tmp_path)
    for key in ("POSTGRES_PASSWORD", "S3_ACCESS_KEY", "S3_SECRET_KEY", "JACARA_MODE"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_env_is_created_once_and_private(project):
    step = ensure_env(project)
    env = project / ".env"
    assert step.ok and env.is_file()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600  # Owner-readable only.
    first = env.read_text("utf-8")
    again = ensure_env(project)
    assert again.ok and "不动" in again.detail
    assert env.read_text("utf-8") == first  # An existing .env is not overwritten.


def test_existing_env_with_placeholders_is_reported(project):
    (project / ".env").write_text("POSTGRES_PASSWORD=change-me\n", "utf-8")
    step = ensure_env(project)
    assert not step.ok and "POSTGRES_PASSWORD" in step.detail


def test_docker_missing_gives_a_hint(project, monkeypatch):
    monkeypatch.setattr(setup.shutil, "which", lambda _name: None)
    step = setup.docker_up(project)
    assert not step.ok and "Docker Desktop" in step.hint
    llama = setup.llama_ready()
    assert not llama.ok and "brew install llama.cpp" in llama.hint


def test_run_setup_reports_each_step(project, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(setup, "migrate", lambda: Step("建表", True, "数据库是最新的"))
    monkeypatch.setattr(setup, "llama_ready", lambda: Step("llama-server", True, "找到了"))
    monkeypatch.setattr(setup, "web_ready", lambda: Step("网页", True, "在"))
    monkeypatch.setattr(
        setup,
        "models_ready",
        lambda: [
            Step("大模型", False, "没有", setup.MODEL_URL),
            Step("视觉模型", False, "没有", required=False),
        ],
    )
    code = run_setup(project, docker=False, out=lines.append)
    assert code == 1  # The LLM is still missing.
    text = "\n".join(lines)
    assert "✓ .env" in text and "✓ 建表" in text
    assert "✗ 大模型" in text and setup.MODEL_URL in text
    assert "· 视觉模型" in text  # Optional items do not count as missing.
    assert "还差 1 步" in text
