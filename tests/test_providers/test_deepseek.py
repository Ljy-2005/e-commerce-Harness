"""DeepSeek Provider 单元测试 — Mock HTTP 层（不发起真实网络请求）

覆盖 V4 代（2026-08 起）：默认模型 deepseek-v4-flash、视觉 chat_with_vision、
JSON 模式解析、API 错误路径。
"""

import httpx as _httpx_module
import pytest

from src.providers.deepseek import DeepSeekLLMProvider


class _FakeResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers or {}, "json": json})
        return self._response


@pytest.fixture
def deepseek_http(monkeypatch):
    state = {"response": _FakeResponse(200, {}), "client": None}

    def _make_client(*args, **kwargs):
        state["client"] = _FakeAsyncClient(state["response"])
        return state["client"]

    monkeypatch.setattr(_httpx_module, "AsyncClient", _make_client)
    return state


class TestDeepSeekProvider:
    def test_instantiation(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-test")
        p = DeepSeekLLMProvider()
        assert p.name == "deepseek"
        assert "vision" in p.capabilities  # V4 代新增视觉
        assert "text" in p.capabilities
        assert p.api_key == "sk-ds-test"

    @pytest.mark.asyncio
    async def test_chat_default_model_is_v4_flash(self, deepseek_http, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        deepseek_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": "你好"}}],
            "usage": {"total_tokens": 20, "prompt_tokens": 10, "completion_tokens": 10},
        })
        p = DeepSeekLLMProvider()
        out = await p.chat([{"role": "user", "content": "你好"}])
        sent = deepseek_http["client"].requests[-1]["json"]
        assert sent["model"] == "deepseek-v4-flash"   # 默认已是 V4，非 deepseek-chat
        assert out["content"] == {"text": "你好"}
        assert out["tokens_used"] == 20

    @pytest.mark.asyncio
    async def test_chat_json_mode_parses(self, deepseek_http, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        deepseek_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"total_tokens": 10},
        })
        p = DeepSeekLLMProvider()
        out = await p.chat([{"role": "user", "content": "返回 json"}], json_mode=True)
        sent = deepseek_http["client"].requests[-1]["json"]
        assert sent["response_format"] == {"type": "json_object"}
        assert out["content"] == {"ok": True}

    @pytest.mark.asyncio
    async def test_chat_with_vision_passes_multimodal_messages(self, deepseek_http, monkeypatch):
        """视觉能力：默认模型用 V4 vision-exp，消息透传（OpenAI 兼容多模态格式）"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        deepseek_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": '{"category": "shoes"}'}}],
            "usage": {"total_tokens": 30},
        })
        p = DeepSeekLLMProvider()
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "分析这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]},
        ]
        out = await p.chat_with_vision(messages)
        sent = deepseek_http["client"].requests[-1]["json"]
        assert sent["model"] == "deepseek-v4-flash-vision-exp"
        assert sent["messages"] == messages  # 透传，解析 JSON 结果
        assert out["content"] == {"category": "shoes"}

    @pytest.mark.asyncio
    async def test_chat_api_error(self, deepseek_http, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        deepseek_http["response"] = _FakeResponse(429, {})
        p = DeepSeekLLMProvider()
        out = await p.chat([{"role": "user", "content": "hi"}])
        assert "error" in out and "429" in out["error"]
