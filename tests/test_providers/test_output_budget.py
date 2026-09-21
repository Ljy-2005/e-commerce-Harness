"""LLM 输出的预算与空内容处理（A32）

实测事故：DeepSeek V4 是**推理模型**，`max_tokens: 4096` 被 reasoning token 吃满
（实测 `finish_reason=length`、`reasoning_tokens=4097`、`content=""`），provider 回落
`{"text": ""}` 被当作成功 → 品类专项分析员连续两轮"输出为空"，协调者重试两次后放弃。

要求：
1. 空内容必须变成可读错误（绝不静默成功）；
2. `finish_reason=length` 时自动**一次性**提升预算重试；
3. 提升后仍截断 → 明确报"截断 + 思考占用"；
4. 围栏 JSON 能被解析（A33）。
"""

import httpx as _httpx_module
import pytest

from src.providers.deepseek import DeepSeekLLMProvider
from src.providers.openai import OpenAILLMProvider


class _Resp:
    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.text = text

    def json(self):
        return self._data


class _QueueClient:
    """按请求顺序返回预置响应（与既有测试的"单响应"替身不同：预算升级会发第二次请求）"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers or {}, "json": json})
        return self.responses.pop(0) if self.responses else _Resp(200, {})


@pytest.fixture
def http_queue(monkeypatch):
    state = {"client": None, "responses": []}

    def _make_client(*args, **kwargs):
        # 复用同一个 client：预算升级会再发一次请求，响应队列必须跨请求共享
        if state["client"] is None:
            state["client"] = _QueueClient(state["responses"])
        return state["client"]

    monkeypatch.setattr(_httpx_module, "AsyncClient", _make_client)

    def _install(responses):
        state["responses"] = responses
        state["client"] = None
        return state

    return _install


def _truncated_no_content(reasoning_tokens=4097):
    return _Resp(200, {
        "choices": [{"finish_reason": "length",
                     "message": {"content": "", "reasoning_content": "思考" * 100}}],
        "usage": {"total_tokens": 4249, "prompt_tokens": 152, "completion_tokens": 4097,
                  "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}},
    })


def _ok(content='{"ok": true}', finish="stop"):
    return _Resp(200, {
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {"total_tokens": 20, "prompt_tokens": 10, "completion_tokens": 10},
    })


class TestReasoningTruncation:
    @pytest.mark.asyncio
    async def test_empty_content_length_retries_with_bigger_budget(self, monkeypatch, http_queue):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        state = http_queue([_truncated_no_content(), _ok()])

        out = await DeepSeekLLMProvider().chat([{"role": "user", "content": "hi"}], json_mode=True)

        assert "error" not in out, out
        assert out["content"] == {"ok": True}
        budgets = [r["json"]["max_tokens"] for r in state["client"].requests]
        assert len(budgets) == 2 and budgets[1] > budgets[0], f"应提升预算重试一次: {budgets}"

    @pytest.mark.asyncio
    async def test_still_truncated_reports_actionable_error(self, monkeypatch, http_queue):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        http_queue([_truncated_no_content(), _truncated_no_content()])

        out = await DeepSeekLLMProvider().chat([{"role": "user", "content": "hi"}], json_mode=True)

        assert "error" in out
        assert "截断" in out["error"] and "max_tokens" in out["error"]
        assert "思考" in out["error"], "应指出 token 被思考占用，用户才知道怎么办"

    @pytest.mark.asyncio
    async def test_empty_content_without_length_is_error(self, monkeypatch, http_queue):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        http_queue([_Resp(200, {"choices": [{"finish_reason": "stop", "message": {"content": ""}}],
                               "usage": {"total_tokens": 5}})])

        out = await DeepSeekLLMProvider().chat([{"role": "user", "content": "hi"}])

        assert "error" in out and "空内容" in out["error"]

    @pytest.mark.asyncio
    async def test_truncated_json_in_json_mode_is_reported_as_truncation(self, monkeypatch, http_queue):
        """此前截断的 JSON 只表现为"缺少必要字段"，用户看不懂"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        truncated_json = _Resp(200, {
            "choices": [{"finish_reason": "length",
                         "message": {"content": '{"marketing_angles": {"selling'}}],
            "usage": {"total_tokens": 4097},
        })
        http_queue([truncated_json, truncated_json])

        out = await DeepSeekLLMProvider().chat([{"role": "user", "content": "hi"}], json_mode=True)

        assert "error" in out and "截断" in out["error"]

    @pytest.mark.asyncio
    async def test_fenced_json_is_parsed(self, monkeypatch, http_queue):
        """A33：```json 围栏必须能解析（分析员置信度 0 的根因）"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        http_queue([_ok('```json\n{"confidence_score": 72}\n```')])

        out = await DeepSeekLLMProvider().chat_with_vision([{"role": "user", "content": "hi"}])

        assert out["content"] == {"confidence_score": 72}

    @pytest.mark.asyncio
    async def test_max_tokens_is_configurable(self, monkeypatch, http_queue):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        state = http_queue([_ok()])

        await DeepSeekLLMProvider(max_tokens=16384).chat([{"role": "user", "content": "hi"}])

        assert state["client"].requests[0]["json"]["max_tokens"] == 16384

    @pytest.mark.asyncio
    async def test_usage_tokens_are_returned(self, monkeypatch, http_queue):
        """截断场景也要把用量带出来（否则成本链路看不到这次调用）"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        http_queue([_truncated_no_content(), _truncated_no_content()])

        out = await DeepSeekLLMProvider().chat([{"role": "user", "content": "hi"}])

        assert out.get("tokens_used", 0) > 0


class TestOpenAICompatibleSharesGuard:
    @pytest.mark.asyncio
    async def test_openai_chat_also_guards_empty_content(self, monkeypatch, http_queue):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        http_queue([_truncated_no_content(), _truncated_no_content()])

        out = await OpenAILLMProvider().chat([{"role": "user", "content": "hi"}])

        assert "error" in out and "截断" in out["error"]

    @pytest.mark.asyncio
    async def test_openai_vision_parses_fenced_json(self, monkeypatch, http_queue):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        http_queue([_ok('说明\n```json\n{"verdict": "pass"}\n```')])

        out = await OpenAILLMProvider().chat_with_vision([{"role": "user", "content": "hi"}])

        assert out["content"] == {"verdict": "pass"}
