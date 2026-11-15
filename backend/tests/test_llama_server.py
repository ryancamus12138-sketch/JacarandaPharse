"""llama-server backend: httpx.post is replaced by a fake; no model is started."""

import httpx

from jacara.plugins.model_backends import ChatMessage, ModelBackendError
from jacara.plugins.model_backends.base import ModelConfig
from jacara.plugins.model_backends.llama_server import LlamaServerBackend

from .conftest import raises


def fake_post(finish_reason: str, sent: list[dict], headers_seen: list[dict] | None = None):
    def post(url, json, headers, timeout):
        sent.append(json)
        if headers_seen is not None:
            headers_seen.append(headers)
        body = {
            "model": "fake-model",
            "choices": [{"message": {"content": '{"total"'}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
        }
        return httpx.Response(200, json=body)

    return post


def test_cut_off_answer_says_so(monkeypatch):
    """Answers cut off at max_tokens are reported explicitly rather than as invalid JSON."""
    monkeypatch.setattr(httpx, "post", fake_post("length", []))
    with raises(ModelBackendError, "上下文长度"):
        LlamaServerBackend().chat([ChatMessage("user", "hi")])


def test_normal_answer_has_no_length_limit(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(httpx, "post", fake_post("stop", sent))
    result = LlamaServerBackend().chat([ChatMessage("user", "hi")])
    assert result.text == '{"total"' and result.model == "fake-model"
    # Tables must be complete: answer length is unbounded by default, limited only by the context
    # length.
    assert "max_tokens" not in sent[0]


def test_length_limit_can_still_be_set(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(httpx, "post", fake_post("length", sent))
    with raises(ModelBackendError, "200 个 token"):
        LlamaServerBackend(ModelConfig(max_tokens=200)).chat([ChatMessage("user", "hi")])
    assert sent[0]["max_tokens"] == 200


def test_api_key_is_sent_as_bearer(monkeypatch):
    """The model server started by `jacara start` has an API key: requests carry Authorization."""
    headers: list[dict] = []
    monkeypatch.setattr(httpx, "post", fake_post("stop", [], headers))
    LlamaServerBackend(ModelConfig(api_key="k123")).chat([ChatMessage("user", "hi")])
    LlamaServerBackend().chat([ChatMessage("user", "hi")])
    assert headers == [{"Authorization": "Bearer k123"}, {}]


def test_wrong_key_says_so(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, json, headers, timeout: httpx.Response(401))
    with raises(ModelBackendError, "密钥"):
        LlamaServerBackend(ModelConfig(api_key="old")).chat([ChatMessage("user", "hi")])


def test_server_has_no_web_ui(monkeypatch):
    from jacara.plugins.model_backends import server

    monkeypatch.setattr(server.shutil, "which", lambda name: "/usr/bin/llama-server")
    assert "--no-webui" in server.server_command(server.Path("m.gguf"))


def test_server_has_room_for_long_tables():
    from jacara.plugins.model_backends.server import DEFAULT_CONTEXT

    assert DEFAULT_CONTEXT >= 32768
