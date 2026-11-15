"""Model backend extension point: defines how to talk to the local LLM.

Extraction, validation and other steps depend only on these types, regardless of whether
llama-server, Ollama or a future vLLM runs behind them.

Two ways to change the model:
1. Same server, different model file (for example 9B to 4B): start the server with another .gguf
   file; no code changes.
2. Different server (for example vLLM): implement ModelBackend in a new module in this package
   and register it in __init__.py. No other code changes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


class ModelBackendError(RuntimeError):
    """The model server is unreachable or returned an error. The message is shown to the user as is,
    so it explains the remedy."""


@dataclass(frozen=True)
class ModelConfig:
    """Common model backend configuration."""

    backend: str = "llama-server"
    """Backend plugin to use; the name must be registered in __init__.py."""

    base_url: str = "http://127.0.0.1:8080"
    """Model server address. Local addresses only, keeping the system fully offline."""

    timeout_s: float = 600.0
    """Maximum seconds to wait for an answer. Answers are unbounded; receipts with many items may
    take several minutes."""

    temperature: float = 0.0
    """Sampling temperature. Field extraction must be stable and reproducible, hence 0 by
    default."""

    max_tokens: int | None = None
    """Maximum tokens to generate. None: unbounded; the model writes until the answer is complete
    (limited only by the context length).

        Earlier limits of 1024 and 4096 truncated answers for receipts with many items, leaving
    tables incomplete and failing the whole document. Tables must be complete, so there is no
    limit; the JSON Schema guarantees the model stops once the JSON is complete."""

    enable_thinking: bool = False
    """Whether the model "thinks" before answering. Unnecessary for field extraction; disabled for
    speed."""

    extra: dict[str, object] = field(default_factory=dict)
    api_key: str | None = None
    """Model server API key (required by the llama-server started by `jacara start`), sent in the
    Authorization header."""
    """Backend-specific advanced parameters, passed into the request unchanged."""


@dataclass(frozen=True)
class ChatMessage:
    """One chat message. system holds rules for the model, user the question, assistant the model
    answer."""

    role: Role
    content: str
    images: tuple[bytes, ...] = ()
    """Attached images (PNG), placed after the text. Understood only when the model server has a
    vision projector loaded."""


@dataclass(frozen=True)
class ChatResult:
    """One model answer and its provenance.

        model and backend must be recorded so that every extraction can be traced to the model
    used."""

    text: str
    model: str
    backend: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int


class ModelBackend(Protocol):
    """Model backend interface: any class with a name attribute and a chat method is a model
    backend.

        Backends that can process images also implement sees_images() -> bool; without it they
    are assumed not to."""

    name: str

    def chat(
        self, messages: list[ChatMessage], *, json_schema: dict[str, object] | None = None
    ) -> ChatResult:
        """Send messages and return the model answer.

                With json_schema, the model can only output JSON matching the schema (enforced by
        the server during generation)."""
        ...
