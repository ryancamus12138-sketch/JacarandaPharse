"""llama-server 后端：通过 HTTP 调用本机的 llama-server（llama.cpp 自带的模型服务）。

llama-server 实现了 OpenAI 格式的 /v1/chat/completions 接口，所以这里的写法
也适用于其他兼容这个格式的本地服务。
"""

from __future__ import annotations

import time

import httpx

from jacara.plugins.model_backends.base import (
    ChatMessage,
    ChatResult,
    ModelBackendError,
    ModelConfig,
)


class LlamaServerBackend:
    name = "llama-server"

    def __init__(self, config: ModelConfig | None = None) -> None:
        self.config = config or ModelConfig()

    def _payload(
        self, messages: list[ChatMessage], json_schema: dict[str, object] | None
    ) -> dict[str, object]:
        """把通用的消息和配置翻译成 llama-server 要的请求内容。"""
        c = self.config
        payload: dict[str, object] = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
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
            response = httpx.post(
                url, json=self._payload(messages, json_schema), timeout=self.config.timeout_s
            )
        except httpx.ConnectError as err:
            raise ModelBackendError(
                f"连不上模型服务 {self.config.base_url}。请先运行 `uv run jacara serve` 启动模型。"
            ) from err
        except httpx.TimeoutException as err:
            raise ModelBackendError(
                f"模型超过 {self.config.timeout_s:.0f} 秒没有回答，可以调大 timeout_s。"
            ) from err

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
