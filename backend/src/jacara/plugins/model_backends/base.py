"""模型后端扩展点：规定"怎么跟本地大模型说话"。

抽取、校验等步骤只认这里的类型，不关心背后是 llama-server、Ollama 还是以后的 vLLM。

换模型有两种情况：
1. 同一个服务，换模型文件（例如 9B 换成 4B）：启动服务时换 .gguf 文件，代码不用改。
2. 换一种服务（例如 vLLM）：在本目录新建一个文件实现 ModelBackend，
   再在 __init__.py 里登记一行。其他代码都不用改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Role = Literal["system", "user", "assistant"]


class ModelBackendError(RuntimeError):
    """连不上模型服务，或服务返回了错误。报错信息直接给人看，所以写清楚怎么解决。"""


@dataclass(frozen=True)
class ModelConfig:
    """模型后端的通用配置。"""

    backend: str = "llama-server"
    """用哪个后端插件，名字要在 __init__.py 的登记表里。"""

    base_url: str = "http://127.0.0.1:8080"
    """模型服务的地址。只用本机地址，符合"完全离线"的规则。"""

    timeout_s: float = 120.0
    """等模型回答最多多少秒。大模型在 CPU 上可能很慢。"""

    temperature: float = 0.0
    """随机程度。抽字段要稳定、可重复，所以默认 0。"""

    max_tokens: int = 1024
    """最多生成多少个 token，防止模型一直说下去。"""

    enable_thinking: bool = False
    """是否让模型先"思考"再回答。抽字段不需要，关掉更快。"""

    extra: dict[str, object] = field(default_factory=dict)
    """各后端特有的高级参数，原样放进请求里。"""


@dataclass(frozen=True)
class ChatMessage:
    """对话里的一条消息。system 是给模型的规则，user 是问题，assistant 是模型的回答。"""

    role: Role
    content: str


@dataclass(frozen=True)
class ChatResult:
    """模型的一次回答，以及"它是怎么来的"。

    model 和 backend 必须记录：项目规则要求每次抽取都能追溯到用的是哪个模型。
    """

    text: str
    model: str
    backend: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: int


class ModelBackend(Protocol):
    """模型后端接口：任何类只要有 name 属性和 chat 方法，就是一个模型后端。"""

    name: str

    def chat(
        self, messages: list[ChatMessage], *, json_schema: dict[str, object] | None = None
    ) -> ChatResult:
        """发一组消息，拿回模型的回答。

        给了 json_schema 时，模型只能输出符合这个格式的 JSON（由服务端在生成时强制保证）。
        """
        ...
