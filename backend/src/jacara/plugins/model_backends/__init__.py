"""模型后端登记表：按配置里的名字创建对应的模型后端。

新增一个后端（例如 vLLM）：
1. 在本目录新建 vllm.py，写一个实现 ModelBackend 接口的类；
2. 在下面的 _BACKENDS 里加一行 "vllm": VllmBackend。
调用方只写 create_model_backend(config)，永远不需要知道具体是哪个类。
"""

from collections.abc import Callable

from jacara.plugins.model_backends.base import (
    ChatMessage,
    ChatResult,
    ModelBackend,
    ModelBackendError,
    ModelConfig,
)
from jacara.plugins.model_backends.llama_server import LlamaServerBackend

_BACKENDS: dict[str, Callable[[ModelConfig], ModelBackend]] = {
    "llama-server": LlamaServerBackend,
}


def available_backends() -> list[str]:
    """列出所有登记过的模型后端名字。"""
    return sorted(_BACKENDS)


def create_model_backend(config: ModelConfig | None = None) -> ModelBackend:
    """按配置创建模型后端。名字没登记过就报错，并列出可用的名字。"""
    config = config or ModelConfig()
    factory = _BACKENDS.get(config.backend)
    if factory is None:
        raise ValueError(f"未知的模型后端：{config.backend}，可用的有：{available_backends()}")
    return factory(config)


__all__ = [
    "ChatMessage",
    "ChatResult",
    "LlamaServerBackend",
    "ModelBackend",
    "ModelBackendError",
    "ModelConfig",
    "available_backends",
    "create_model_backend",
]
