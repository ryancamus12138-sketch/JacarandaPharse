"""llama-server 后端：把 httpx.post 换成假的，不用真的启动模型。"""

import httpx
import pytest

from jacara.plugins.model_backends import ChatMessage, ModelBackendError
from jacara.plugins.model_backends.base import ModelConfig
from jacara.plugins.model_backends.llama_server import LlamaServerBackend


def fake_post(finish_reason: str, sent: list[dict]):
    def post(url, json, timeout):
        sent.append(json)
        body = {
            "model": "fake-model",
            "choices": [{"message": {"content": '{"total"'}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
        }
        return httpx.Response(200, json=body)

    return post


def test_cut_off_answer_says_so(monkeypatch):
    """回答写到 max_tokens 被截断：直接说清楚，而不是报"不是合格的 JSON"。"""
    monkeypatch.setattr(httpx, "post", fake_post("length", []))
    with pytest.raises(ModelBackendError, match="上下文长度"):
        LlamaServerBackend().chat([ChatMessage("user", "hi")])


def test_normal_answer_has_no_length_limit(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(httpx, "post", fake_post("stop", sent))
    result = LlamaServerBackend().chat([ChatMessage("user", "hi")])
    assert result.text == '{"total"' and result.model == "fake-model"
    # 表格必须完整：默认不限制回答长度，只受上下文长度限制
    assert "max_tokens" not in sent[0]


def test_length_limit_can_still_be_set(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(httpx, "post", fake_post("length", sent))
    with pytest.raises(ModelBackendError, match="200 个 token"):
        LlamaServerBackend(ModelConfig(max_tokens=200)).chat([ChatMessage("user", "hi")])
    assert sent[0]["max_tokens"] == 200


def test_server_has_room_for_long_tables():
    from jacara.plugins.model_backends.server import DEFAULT_CONTEXT

    assert DEFAULT_CONTEXT >= 32768
