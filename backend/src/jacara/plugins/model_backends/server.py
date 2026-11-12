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


DEFAULT_CONTEXT = 32768
"""上下文长度：提示词和回答加起来最多多少个 token。

回答不设上限（商品明细必须完整），所以真正的上限是它。一张很长的收据，提示词约 3000、
回答每行商品约 30 个 token，32768 能放下几百行。原来是 8192。
"""


def server_command(
    model: Path,
    *,
    port: int = 8080,
    context: int = DEFAULT_CONTEXT,
    mmproj: Path | None = None,
) -> list[str]:
    """拼出启动命令。找不到 llama-server 时报错并说明怎么安装。

    mmproj 是视觉模型文件（ADR 0038）：给了模型就能看图。"""
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
        "--no-webui",  # 不要 llama-server 自带的聊天网页：只当接口用
        *(["--mmproj", str(mmproj)] if mmproj is not None else []),
    ]  # fmt: skip


def run_server(
    model: Path,
    *,
    port: int = 8080,
    context: int = DEFAULT_CONTEXT,
    mmproj: Path | None = None,
) -> int:
    """在前台运行 llama-server，按 Ctrl + C 停止。返回退出码。"""
    command = server_command(model, port=port, context=context, mmproj=mmproj)
    try:
        # 命令里只有本机找到的程序路径和我们自己拼的参数，没有外部输入。
        return subprocess.run(command, env=server_env(), check=False).returncode  # noqa: S603
    except KeyboardInterrupt:
        return 0
