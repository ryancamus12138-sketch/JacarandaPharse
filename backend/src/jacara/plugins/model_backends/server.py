"""Start the local llama-server.

Users should not need to remember launch parameters and environment variables; they are handled
here. Future background services will start the model through this module too."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from jacara.core.i18n import stored


def server_env() -> dict[str, str]:
    """Environment variables for launching llama-server.

        On macOS 14 and 15, bfloat code in the Metal shaders of recent llama.cpp fails to compile
    and GPU initialization fails (llama.cpp issue #31272). Disabling bfloat restores GPU use;
    quantized models such as Q4_K_M are unaffected. Remove once fixed upstream."""
    env = os.environ.copy()
    if sys.platform == "darwin":
        env.setdefault("GGML_METAL_BF16_DISABLE", "1")
    return env


DEFAULT_CONTEXT = 32768
"""Context length: maximum tokens of prompt and answer combined.

Answers are unbounded (line items must be complete), so this is the effective limit. For a very
long receipt the prompt is about 3000 tokens and each item line about 30, so 32768 holds several
hundred lines. Previously 8192."""


def server_command(
    model: Path,
    *,
    port: int = 8080,
    context: int = DEFAULT_CONTEXT,
    mmproj: Path | None = None,
) -> list[str]:
    """Build the launch command. Raises with installation instructions when llama-server is not
    found.

        mmproj is the vision projector file; when given, the model can process images."""
    exe = shutil.which("llama-server")
    if exe is None:
        raise FileNotFoundError(
            stored("找不到 llama-server。Mac 上请先运行：brew install llama.cpp")
        )
    return [
        exe,
        "-m", str(model),
        "--alias", model.stem,  # Model name reported in answers; recorded in extraction results.
        "--host", "127.0.0.1",  # Local access only.
        "--port", str(port),
        "-c", str(context),
        "--no-webui",  # Disable the built-in chat web UI of llama-server; API only.
        *(["--mmproj", str(mmproj)] if mmproj is not None else []),
    ]  # fmt: skip


def run_server(
    model: Path,
    *,
    port: int = 8080,
    context: int = DEFAULT_CONTEXT,
    mmproj: Path | None = None,
) -> int:
    """Run llama-server in the foreground; stop with Ctrl + C. Returns the exit code."""
    command = server_command(model, port=port, context=context, mmproj=mmproj)
    try:
        # The command contains only the locally found executable path and arguments we build; no
        # external input.
        return subprocess.run(command, env=server_env(), check=False).returncode  # noqa: S603
    except KeyboardInterrupt:
        return 0
