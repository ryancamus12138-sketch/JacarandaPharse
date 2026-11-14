"""llama-server backend: calls the local llama-server (the model server bundled with llama.cpp) over
HTTP.

llama-server implements the OpenAI-compatible /v1/chat/completions endpoint, so this code also
works with other local servers compatible with that format."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable

import httpx

from jacara.core.i18n import N_, stored
from jacara.plugins.model_backends.base import (
    ChatMessage,
    ChatResult,
    ModelBackendError,
    ModelConfig,
)

WRONG_KEY = N_("模型服务要密钥，但密钥不对。模型是 jacara start 启动的话，请重新运行 jacara start")

VISION_CACHE_S = 60
"""Once queried, the image capability is cached for one minute."""


def _message(m: ChatMessage) -> dict[str, object]:
    """One message. Messages with images use the OpenAI multi-part format: text first, then each
    image (base64 data URL)."""
    if not m.images:
        return {"role": m.role, "content": m.content}
    parts: list[dict[str, object]] = [{"type": "text", "text": m.content}]
    for image in m.images:
        url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": m.role, "content": parts}


class LlamaServerBackend:
    name = "llama-server"

    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig()
        self._vision: tuple[float, bool] | None = None

    def sees_images(self) -> bool:
        """Ask the model server (modalities.vision in /props) whether a vision projector is loaded.
        Cached for one minute."""
        now = time.monotonic()
        if self._vision is not None and now - self._vision[0] < VISION_CACHE_S:
            return self._vision[1]
        try:
            response = httpx.get(
                f"{self.config.base_url}/props", headers=self._headers(), timeout=2
            )
            data = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            data = {}
        modalities = data.get("modalities") if isinstance(data, dict) else None
        sees = bool(isinstance(modalities, dict) and modalities.get("vision"))
        self._vision = (now, sees)
        return sees

    def _headers(self) -> dict[str, str]:
        key = self.config.api_key
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _payload(
        self, messages: list[ChatMessage], json_schema: dict[str, object] | None
    ) -> dict[str, object]:
        """Translate generic messages and configuration into a llama-server request body."""
        c = self.config
        payload: dict[str, object] = {
            "messages": [_message(m) for m in messages],
            "temperature": c.temperature,
            "chat_template_kwargs": {"enable_thinking": c.enable_thinking},
        }
        if c.max_tokens is not None:
            payload["max_tokens"] = c.max_tokens
        if json_schema is not None:
            # The server constrains the model output to this schema; the model cannot produce
            # anything else.
            payload["response_format"] = {"type": "json_object", "schema": json_schema}
        payload.update(c.extra)
        return payload

    def chat(
        self, messages: list[ChatMessage], *, json_schema: dict[str, object] | None = None
    ) -> ChatResult:
        url = f"{self.config.base_url}/v1/chat/completions"
        start = time.perf_counter()
        try:
            headers = self._headers()
            response = httpx.post(
                url,
                json=self._payload(messages, json_schema),
                headers=headers,
                timeout=self.config.timeout_s,
            )
        except httpx.ConnectError as err:
            raise ModelBackendError(
                stored(
                    "连不上模型服务 {base_url}。请先运行 `uv run jacara serve` 启动模型。",
                    base_url=self.config.base_url,
                )
            ) from err
        except httpx.TimeoutException as err:
            raise ModelBackendError(
                stored(
                    "模型超过 {timeout_s:.0f} 秒没有回答，可以调大 timeout_s。",
                    timeout_s=self.config.timeout_s,
                )
            ) from err

        if response.status_code == 401:
            raise ModelBackendError(WRONG_KEY)
        if response.status_code != 200:
            detail = response.text[:300]
            raise ModelBackendError(
                stored(
                    "模型服务报错（{status_code}）：{detail}",
                    status_code=response.status_code,
                    detail=detail,
                )
            )

        data = response.json()
        usage = data.get("usage", {})
        if data["choices"][0].get("finish_reason") == "length":
            # An answer cut off at max_tokens is necessarily incomplete JSON; report that
            # explicitly.
            limit = (
                stored("{max_tokens} 个 token", max_tokens=self.config.max_tokens)
                if self.config.max_tokens is not None
                else stored("模型服务的上下文长度")
            )
            raise ModelBackendError(
                stored(
                    "模型的回答太长，写到 {limit} 被截断了。可以调大上下文长度（jacara serve "
                    "--context）后重试。",
                    limit=limit,
                )
            )
        return ChatResult(
            text=data["choices"][0]["message"]["content"],
            model=data.get("model", "unknown"),
            backend=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=round((time.perf_counter() - start) * 1000),
        )

    def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        json_schema: dict[str, object] | None = None,
        on_text: Callable[[int], None] | None = None,
    ) -> ChatResult:
        """Like chat, but streams the answer and reports the total characters written to on_text
        after each chunk; progress follows it."""
        url = f"{self.config.base_url}/v1/chat/completions"
        start = time.perf_counter()
        payload = {
            **self._payload(messages, json_schema),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        parts: list[str] = []
        written = 0
        model, finish, usage = "unknown", None, {}
        try:
            with httpx.stream(
                "POST", url, json=payload, headers=self._headers(), timeout=self.config.timeout_s
            ) as response:
                if response.status_code == 401:
                    raise ModelBackendError(WRONG_KEY)
                if response.status_code != 200:
                    detail = response.read().decode("utf-8", "replace")[:300]
                    raise ModelBackendError(
                        stored(
                            "模型服务报错（{status_code}）：{detail}",
                            status_code=response.status_code,
                            detail=detail,
                        )
                    )
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    chunk = json.loads(body)
                    model = chunk.get("model", model)
                    usage = chunk.get("usage") or usage
                    before = written
                    for choice in chunk.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content") or ""
                        if piece:
                            parts.append(piece)
                            written += len(piece)
                        finish = choice.get("finish_reason") or finish
                    if on_text is not None and written > before:
                        on_text(written)
        except httpx.ConnectError as err:
            raise ModelBackendError(
                stored(
                    "连不上模型服务 {base_url}。请先运行 `uv run jacara serve` 启动模型。",
                    base_url=self.config.base_url,
                )
            ) from err
        except httpx.TimeoutException as err:
            raise ModelBackendError(
                stored(
                    "模型超过 {timeout_s:.0f} 秒没有回答，可以调大 timeout_s。",
                    timeout_s=self.config.timeout_s,
                )
            ) from err
        if finish == "length":
            raise ModelBackendError(
                stored(
                    "模型的回答太长，写到模型服务的上下文长度被截断了。"
                    "可以调大上下文长度（jacara serve --context）后重试。"
                )
            )
        return ChatResult(
            text="".join(parts),
            model=model,
            backend=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=round((time.perf_counter() - start) * 1000),
        )
