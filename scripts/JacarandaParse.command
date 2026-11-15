#!/usr/bin/env bash
# macOS launcher: double-click to start JacarandaParse and open the browser.
# Closing the terminal window or pressing Ctrl + C stops all services.
# Run ./scripts/install.sh once before first use.
cd "$(dirname "$0")/.."
exec uv run --frozen --no-dev jacara start
