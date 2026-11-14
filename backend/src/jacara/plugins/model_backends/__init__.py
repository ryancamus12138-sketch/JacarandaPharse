"""Model backend registry: create the backend named in the configuration.

To add a backend (for example vLLM):
1. Create vllm.py in this package with a class implementing ModelBackend;
2. Add "vllm": VllmBackend to _BACKENDS below.
Callers only use create_model_backend(config) and never need the concrete class."""

from collections.abc import Callable

from jacara.core.i18n import join_items, stored
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
    """Names of all registered model backends."""
    return sorted(_BACKENDS)


def create_model_backend(config: ModelConfig | None = None) -> ModelBackend:
    """Create a model backend from the configuration. Raises for unregistered names, listing the
    available ones."""
    config = config or ModelConfig()
    factory = _BACKENDS.get(config.backend)
    if factory is None:
        raise ValueError(
            stored(
                "未知的模型后端：{backend}，可用后端：{available}",
                backend=config.backend,
                available=join_items(available_backends()),
            )
        )
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
