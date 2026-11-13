"""llama-server 后端：通过 HTTP 调用本机的 llama-server（llama.cpp 自带的模型服务）。

llama-server 实现了 OpenAI 格式的 /v1/chat/completions 接口，所以这里的写法
也适用于其他兼容这个格式的本地服务。
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable

import httpx

from jacara.plugins.model_backends.base import (
    ChatMessage,
    ChatResult,
    ModelBackendError,
    ModelConfig,
)

WRONG_KEY = "模型服务要密钥，但密钥不对。模型是 jacara start 启动的话，请重新运行 jacara start"

VISION_CACHE_S = 60
"""问过一次"能不能看图"之后，一分钟内不再问。"""


def _message(m: ChatMessage) -> dict[str, object]:
    """一条消息。带图的写成 OpenAI 的多段格式：先文字，再一张张图（base64 的 data URL）。"""
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
        """问模型服务（/props 的 modalities.vision）加载了视觉模型没有。结果记一分钟。"""
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
        """把通用的消息和配置翻译成 llama-server 要的请求内容。"""
        c = self.config
        payload: dict[str, object] = {
            "messages": [_message(m) for m in messages],
            "temperature": c.temperature,
            "chat_template_kwargs": {"enable_thinking": c.enable_thinking},
        }
        if c.max_tokens is not None:
            payload["max_tokens"] = c.max_tokens
        if json_schema is not None:
            # 服务端会按这个格式限制模型的输出，模型"想"说别的也说不出来。
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
                f"连不上模型服务 {self.config.base_url}。请先运行 `uv run jacara serve` 启动模型。"
            ) from err
        except httpx.TimeoutException as err:
            raise ModelBackendError(
                f"模型超过 {self.config.timeout_s:.0f} 秒没有回答，可以调大 timeout_s。"
            ) from err

        if response.status_code == 401:
            raise ModelBackendError(WRONG_KEY)
        if response.status_code != 200:
            detail = response.text[:300]
            raise ModelBackendError(f"模型服务报错（{response.status_code}）：{detail}")

        data = response.json()
        usage = data.get("usage", {})
        if data["choices"][0].get("finish_reason") == "length":
            # 回答写到 max_tokens 就被截断了，后面拿到的一定是半截 JSON，不如直接说清楚
            limit = (
                f"{self.config.max_tokens} 个 token"
                if self.config.max_tokens is not None
                else "模型服务的上下文长度"
            )
            raise ModelBackendError(
                f"模型的回答太长，写到 {limit} 被截断了。"
                "可以调大上下文长度（jacara serve --context）后重试。"
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
        """和 chat 一样，但让模型边写边传回来；每收到一段就告诉 on_text 一共写了多少字。
        处理进度按它走（模型写了多少）。"""
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
                    raise ModelBackendError(f"模型服务报错（{response.status_code}）：{detail}")
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
                f"连不上模型服务 {self.config.base_url}。请先运行 `uv run jacara serve` 启动模型。"
            ) from err
        except httpx.TimeoutException as err:
            raise ModelBackendError(
                f"模型超过 {self.config.timeout_s:.0f} 秒没有回答，可以调大 timeout_s。"
            ) from err
        if finish == "length":
            raise ModelBackendError(
                "模型的回答太长，写到模型服务的上下文长度被截断了。"
                "可以调大上下文长度（jacara serve --context）后重试。"
            )
        return ChatResult(
            text="".join(parts),
            model=model,
            backend=self.name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=round((time.perf_counter() - start) * 1000),
        )
