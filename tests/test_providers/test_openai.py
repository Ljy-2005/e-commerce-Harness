"""OpenAI Provider 单元测试 — Mock HTTP 层（不发起真实网络请求）"""

import httpx as _httpx_module
import pytest

from src.providers.openai import OpenAIImageProvider, OpenAILLMProvider

# ── 假 HTTP 层 ──


class _FakeResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data

    @property
    def text(self) -> str:
        """真实 httpx.Response 有 .text —— Provider 错误串会带上上游原文（便于定位故障）"""
        import json as _json
        return _json.dumps(self._data, ensure_ascii=False)


class _FakeAsyncClient:
    """替换 httpx.AsyncClient：捕获请求，返回预设响应"""

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
def openai_http(monkeypatch):
    """把 httpx.AsyncClient 换成假客户端（Provider 方法内部 import httpx，patch 模块属性即可生效）"""
    state = {"response": _FakeResponse(200, {}), "client": None}

    def _make_client(*args, **kwargs):
        state["client"] = _FakeAsyncClient(state["response"])
        return state["client"]

    monkeypatch.setattr(_httpx_module, "AsyncClient", _make_client)
    return state


# ── OpenAILLMProvider ──


class TestOpenAILLMProvider:
    def test_instantiation(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        p = OpenAILLMProvider()
        assert p.name == "openai"
        assert "vision" in p.capabilities
        assert "text" in p.capabilities
        assert p.api_key == "sk-test-123"

    @pytest.mark.asyncio
    async def test_chat_json_mode_parses_content(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": '{"action": "done", "reasoning": "ok"}'}}],
            "usage": {"total_tokens": 100, "prompt_tokens": 40, "completion_tokens": 60},
        })
        p = OpenAILLMProvider()
        result = await p.chat(
            [{"role": "user", "content": "respond with json"}],
            model="gpt-4o",
            json_mode=True,
        )
        assert result["content"] == {"action": "done", "reasoning": "ok"}
        assert result["tokens_used"] == 100
        assert result["tokens_in"] == 40
        assert result["tokens_out"] == 60
        assert result["cost_usd"] == round((40 / 1e6) * 2.50 + (60 / 1e6) * 10.00, 6)

        req = openai_http["client"].requests[0]
        assert req["url"].endswith("/chat/completions")
        assert req["headers"]["Authorization"] == "Bearer sk-test"
        assert req["json"]["model"] == "gpt-4o"
        assert req["json"]["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_chat_injects_json_hint_when_missing(self, openai_http, monkeypatch):
        """消息中无 json 关键词时自动注入 JSON 响应提示（OpenAI 硬性要求）"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5},
        })
        p = OpenAILLMProvider()
        await p.chat([{"role": "user", "content": "你好"}], json_mode=True)
        messages = openai_http["client"].requests[0]["json"]["messages"]
        assert messages[0]["role"] == "system"
        assert "JSON" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_chat_plain_mode_returns_text(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": "你好，这是回复"}}],
            "usage": {"total_tokens": 20, "prompt_tokens": 8, "completion_tokens": 12},
        })
        p = OpenAILLMProvider()
        result = await p.chat([{"role": "user", "content": "你好"}])
        assert result["content"] == {"text": "你好，这是回复"}

    @pytest.mark.asyncio
    async def test_chat_non_json_response_falls_back_to_raw(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": "这不是 JSON"}}],
            "usage": {"total_tokens": 5, "prompt_tokens": 2, "completion_tokens": 3},
        })
        p = OpenAILLMProvider()
        result = await p.chat([{"role": "user", "content": "hi json"}], json_mode=True)
        assert result["content"] == {"raw": "这不是 JSON"}

    @pytest.mark.asyncio
    async def test_chat_api_error_returns_error_dict(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(401, {"error": {"message": "bad key"}})
        p = OpenAILLMProvider()
        result = await p.chat([{"role": "user", "content": "hi"}])
        # 错误串含：状态码 + 生效端点 + 上游原文（配错 Key/端点/模型时可直接定位）
        assert result["error"].startswith("OpenAI API error: 401")
        assert "https://api.openai.com/v1" in result["error"]
        assert "bad key" in result["error"]

    @pytest.mark.asyncio
    async def test_error_message_masks_api_key(self, openai_http, monkeypatch):
        """上游若回显请求信息，密钥必须被遮蔽（绝不进日志/界面）"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-xyz")
        openai_http["response"] = _FakeResponse(400, {"error": {"message": "key sk-secret-xyz invalid"}})
        result = await OpenAILLMProvider().chat([{"role": "user", "content": "hi"}])
        assert "sk-secret-xyz" not in result["error"]
        assert "***" in result["error"]

    @pytest.mark.asyncio
    async def test_chat_with_vision(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": '{"category": "保健品"}'}}],
            "usage": {"total_tokens": 30, "prompt_tokens": 10, "completion_tokens": 20},
        })
        p = OpenAILLMProvider()
        result = await p.chat_with_vision(
            [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]}],
        )
        assert result["content"] == {"category": "保健品"}
        req = openai_http["client"].requests[0]
        assert req["url"].endswith("/chat/completions")
        assert req["json"]["max_tokens"] == 4096

    @pytest.mark.asyncio
    async def test_chat_with_vision_api_error(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(500, {})
        p = OpenAILLMProvider()
        result = await p.chat_with_vision([{"role": "user", "content": "hi"}])
        assert result["error"].startswith("OpenAI API error: 500")
        assert "https://api.openai.com/v1" in result["error"]

    def test_estimate_cost_known_models(self):
        p = OpenAILLMProvider()
        assert p._estimate_cost("gpt-4o", 1_000_000, 1_000_000) == 12.50
        assert p._estimate_cost("gpt-4o-mini", 1_000_000, 0) == 0.15
        assert p._estimate_cost("gpt-4.1", 0, 1_000_000) == 8.00
        assert p._estimate_cost("o4-mini", 1_000_000, 1_000_000) == 5.50

    def test_estimate_cost_unknown_model_is_none(self):
        """未标定的模型 → `None`（**不回落 gpt-4o 的 2.50/10.00 价**）

        此前 `prices.get(model, (2.50, 10.00))` 把任何未知模型（方舟豆包、自定义
        服务商的新模型）都按 gpt-4o 计价 —— 正是用户说的"误导"。
        """
        p = OpenAILLMProvider()
        assert p._estimate_cost("gpt-99", 1_000_000, 0) is None
        assert p._estimate_cost("doubao-seed-2-1-pro-260628", 1_000_000, 0) is None

    def test_estimate_cost_user_price_wins(self, monkeypatch):
        """用户在 `config/pricing.yaml` 填了价 → 立刻按它算（设置页可改的真实含义）"""
        from src.harness import pricing
        from src.harness.pricing import save_prices

        path = pricing.pricing_path()
        original = path.read_bytes() if path.exists() else None
        try:
            save_prices({"gpt-99": {"unit": "1M_tokens", "in": 0.5, "out": 1.5,
                                    "updated_at": "2026-09-18"}})
            p = OpenAILLMProvider()
            assert p._estimate_cost("gpt-99", 1_000_000, 1_000_000) == 2.0
        finally:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
            pricing._reset_cache()

    @pytest.mark.asyncio
    async def test_chat_unknown_model_reports_null_cost(self, openai_http, monkeypatch):
        """返回 `cost_usd: None`（键必须在，值为 null）—— 而不是缺键或 0"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"total_tokens": 30, "prompt_tokens": 10, "completion_tokens": 20},
        })
        result = await OpenAILLMProvider().chat(
            [{"role": "user", "content": "hi"}], model="gpt-99")
        assert "cost_usd" in result
        assert result["cost_usd"] is None
        assert result["tokens_used"] == 30, "用量是事实，必须照报"


# ── OpenAIImageProvider ──


class TestOpenAIImageProvider:
    def test_instantiation(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        p = OpenAIImageProvider()
        assert p.name == "openai"
        assert p.capabilities == ["image"]

    @pytest.mark.asyncio
    async def test_generate_success(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {
            "data": [{"url": "https://img.example.com/1.png", "revised_prompt": "revised prompt"}],
        })
        p = OpenAIImageProvider()
        # 注册表在真实链路里会注入 route（价格按「路由/模型」查）
        p.route = "openai"
        result = await p.generate("a bottle on white background", size="1024x1024", model="dall-e-3")
        assert result["image_url"] == "https://img.example.com/1.png"
        assert result["revised_prompt"] == "revised prompt"
        assert result["model_used"] == "dall-e-3"
        assert result["cost_usd"] == 0.04

        req = openai_http["client"].requests[0]
        assert req["url"].endswith("/images/generations")
        assert req["json"]["prompt"] == "a bottle on white background"
        assert req["json"]["size"] == "1024x1024"
        assert req["json"]["n"] == 1

    @pytest.mark.asyncio
    async def test_generate_api_error(self, openai_http, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(400, {})
        p = OpenAIImageProvider()
        result = await p.generate("x")
        assert result["error"].startswith("DALL-E API error: 400")
        assert "https://api.openai.com/v1" in result["error"]

    @pytest.mark.asyncio
    async def test_generate_negative_prompt_warns(self, openai_http, monkeypatch):
        """DALL-E 不支持 negative_prompt → 必须告警提示被忽略"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        openai_http["response"] = _FakeResponse(200, {"data": [{"url": "https://img/1.png"}]})
        p = OpenAIImageProvider()
        with pytest.warns(UserWarning, match="negative_prompt"):
            result = await p.generate("x", negative_prompt="blurry, low quality")
        assert "image_url" in result
        # 请求体中不携带 negative_prompt
        assert "negative_prompt" not in openai_http["client"].requests[0]["json"]

    def test_estimate_cost_by_model_and_size(self):
        """按**模型 + 尺寸**查价（DALL·E 3 两种档位）"""
        p = OpenAIImageProvider(name="openai")
        assert p._estimate_cost("dall-e-3", "1024x1024") == 0.04
        assert p._estimate_cost("dall-e-3", "1024x1792") == 0.08
        assert p._estimate_cost("dall-e-3", "1792x1024") == 0.08

    def test_estimate_cost_uncalibrated_model_is_none(self):
        """方舟 Seedream（2048x2048）→ `None`，**不再落到 DALL·E 的 0.04 兜底**

        这是本轮的核心修复点：旧 `_estimate_cost(size)` 只认三种 DALL·E 尺寸，
        2048x2048 没命中任何键 → 一律 0.04；而我们实际用的就是方舟 2048x2048。
        """
        p = OpenAIImageProvider(name="ark", default_size="2048x2048")
        assert p._estimate_cost("doubao-seedream-5-0-260128", "2048x2048") is None
        assert p._estimate_cost("doubao-seedream-5-0-260128", "1024x1024") is None
        assert p._estimate_cost("某新模型", "1024x1024") is None

    @pytest.mark.asyncio
    async def test_generate_uncalibrated_reports_null_cost(self, openai_http, monkeypatch):
        """方舟路由出图：结果里 `cost_usd=None`（键在、值为 null），张数/模型照报"""
        monkeypatch.setenv("ARK_API_KEY", "ark-test")
        openai_http["response"] = _FakeResponse(200, {
            "data": [{"url": "https://img.example.com/1.png"}],
        })
        p = OpenAIImageProvider(name="ark", default_size="2048x2048")
        result = await p.generate("白底主图", size="2048x2048",
                                  model="doubao-seedream-5-0-260128")
        assert "cost_usd" in result and result["cost_usd"] is None
        assert result["model_used"] == "doubao-seedream-5-0-260128"
