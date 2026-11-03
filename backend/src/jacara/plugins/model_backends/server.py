"""启动本机的 llama-server。

用户不应该自己去记启动参数和环境变量，这些都由这里统一处理。
以后有了后台服务，也是调用这里来启动模型。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def server_env() -> dict[str, str]:
    """启动 llama-server 时要用的环境变量。

    macOS 14、15 上，新版 llama.cpp 的 Metal 程序里有 bfloat 写法编译不过，
    GPU 初始化失败（llama.cpp issue #31272）。关掉 bfloat 就能正常用 GPU，
    Q4_K_M 等量化模型不受影响。等官方修好后删掉这一段。
    """
    env = os.environ.copy()
    if sys.platform == "darwin":
        env.setdefault("GGML_METAL_BF16_DISABLE", "1")
    return env


def server_command(model: Path, *, port: int = 8080, context: int = 8192) -> list[str]:
    """拼出启动命令。找不到 llama-server 时报错并说明怎么安装。"""
    exe = shutil.which("llama-server")
    if exe is None:
        raise FileNotFoundError("找不到 llama-server。Mac 上请先运行：brew install llama.cpp")
    return [
        exe,
        "-m", str(model),
        "--alias", model.stem,  # 回答里的模型名，会记进抽取结果
        "--host", "127.0.0.1",  # 只允许本机访问
        "--port", str(port),
        "-c", str(context),
    ]  # fmt: skip


def run_server(model: Path, *, port: int = 8080, context: int = 8192) -> int:
    """在前台运行 llama-server，按 Ctrl + C 停止。返回退出码。"""
    command = server_command(model, port=port, context=context)
    try:
        # 命令里只有本机找到的程序路径和我们自己拼的参数，没有外部输入。
        return subprocess.run(command, env=server_env(), check=False).returncode  # noqa: S603
    except KeyboardInterrupt:
        return 0
