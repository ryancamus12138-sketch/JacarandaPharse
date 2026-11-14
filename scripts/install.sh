#!/usr/bin/env bash
# JacarandaParse 个人版：第一次安装（macOS、Linux）。ADR 0064。
# 在解压出来的文件夹里运行：./scripts/install.sh
# 要先装好 uv 和 Docker Desktop；第一次要联网（下载 Python 依赖和数据库镜像），装好以后用的时候不联网。
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "没有找到 uv。Mac：brew install uv；其他系统见 https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "没有找到 Docker。请先安装并打开 Docker Desktop：https://www.docker.com/products/docker-desktop/"
  exit 1
fi

echo "安装 Python 依赖（第一次要几分钟）……"
uv sync --frozen --no-dev
uv run --frozen --no-dev jacara setup
