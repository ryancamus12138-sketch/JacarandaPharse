#!/usr/bin/env bash
# Mac 上双击这个文件启动 JacarandaParse（ADR 0064）。会自动打开浏览器；关掉这个终端窗口或按 Ctrl + C 就全部停止。
# 第一次用之前先运行一次 ./scripts/install.sh。
cd "$(dirname "$0")/.."
exec uv run --frozen --no-dev jacara start
