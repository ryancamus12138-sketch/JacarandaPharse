#!/usr/bin/env bash
# JacarandaParse personal edition: first-time installation (macOS, Linux).
# Run from the extracted release folder: ./scripts/install.sh
# Requires uv and Docker Desktop. Installation needs network access (Python dependencies and
# container images); normal operation afterwards is fully offline.
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
