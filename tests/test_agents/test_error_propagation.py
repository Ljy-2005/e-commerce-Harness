"""Agent 错误传播测试（第二轮审计修复）

回归防护：此前各 Agent 用 `result.get("content", self._mock_xxx())` —— Provider 返回
`{"error": ...}`（无 content 键）时默认值生效，**失败被静默替换成 Mock 模板数据**且不带
error，会话照常"成功"结束（配错 Key / 端点 / 模型时用户完全无感知）。
现在统一走 `BaseAgent._content_or_error`：有 error 原样上报。
"""

import pytest

from src.agents.analyst import ProductAnalystAgent
from src.agents.base import BaseAgent
from src.agents.category import CategorySpecialistAgent
from src.agents.compliance import ComplianceAgent
from src.agents.prompt_gen import PromptGeneratorAgent
from src.agents.reviewer import ReviewerAgent
from src.agents.style_analyst import StyleAnalystAgent

ERROR = "OpenAI API error: 401 @ https://api.openai.com/v1 — invalid api key"


@pytest.fixture(autouse=True)
def _isolate_circuit_breakers():
    """隔离全局熔断器单例：本模块连续制造 Provider 失败，达到阈值会把 "openai"
    打到 OPEN 从而污染后续用例（沿用 tests/test_harness/test_integration.py 的写法）"""
    import src.agents.base as agents_base
    saved = dict(agents_base._circuit_breakers)
    agents_base._circuit_breakers.clear()
    yield
    agents_base._circuit_breakers.clear()
    agents_base._circuit_breakers.update(saved)


class _ErrorProvider:
    """真实 Provider 失败时的返回形态：只有 error，没有 content"""

    name = "openai"
    capabilities = ["vision", "text", "image"]

    async def chat(self, messages, model="", json_mode=False):
        return {"error": ERROR}

    async def chat_with_vision(self, messages, model=""):
        return {"error": ERROR}

    async def generate(self, prompt, negative_prompt="", size="1024x1024", model=""):
        return {"error": ERROR}


class _OkProvider:
    """Provider 正常但未给 content（防御路径）→ 才允许回落模板"""

    name = "openai"
    capabilities = ["vision", "text"]

    async def chat(self, messages, model="", json_mode=False):
        return {"tokens_used": 10, "cost_usd": 0.0}

    async def chat_with_vision(self, messages, model=""):
        return {"tokens_used": 10, "cost_usd": 0.0}


def _session(**task):
    # images 必须带**可用图像数据**：A31 之后审查员/合规审查员在没有可访问图片时
    # 会确定性报 NO_IMAGE_ACCESSIBLE 且不再调用 LLM（本模块测的是"Provider 报错
    # 是否被伪装成成功"，需要让调用真正打到 Provider）
    return {
        "session_id": "s-err",
        "tenant_id": "default",
        "turn_count": 1,
        "task": {"product_images": [], "reference_images": [], **task},
        "artifacts": {
            "analysis": {"category": "保健品"},
            "images": [{"prompt_name": "variant_1", "base64_data": _PNG_B64}],
        },
        "messages": [],
    }


# 1×1 PNG（合法图像字节，供 vision 链路使用）
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


CASES = [
    ("商品分析员", ProductAnalystAgent),
    ("品类专项分析员", CategorySpecialistAgent),
    ("提示词生成员", PromptGeneratorAgent),
    ("审查员", ReviewerAgent),
    ("合规审查员", ComplianceAgent),
    ("风格拆解员", StyleAnalystAgent),
]


class TestAgentErrorPropagation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,cls", CASES, ids=[c[0] for c in CASES])
    async def test_provider_error_is_reported_not_masked(self, name, cls):
        """Provider 报错 → Agent 原样上报 error（不返回模板数据）"""
        agent = cls(provider=_ErrorProvider())
        result = await agent.execute("分析上传的商品图片", _session())
        assert result.get("error") == ERROR, f"{name} 把失败伪装成了成功: {str(result)[:120]}"
        assert "category" not in result or result.get("category") is None or "error" in result

    @pytest.mark.asyncio
    async def test_reviewer_error_not_decorated(self):
        """审查员失败时不注入 iteration 等字段（保持错误原样）"""
        result = await ReviewerAgent(provider=_ErrorProvider()).execute("审查", _session())
        assert result.get("error") == ERROR
        assert "iteration" not in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name,cls", CASES, ids=[c[0] for c in CASES])
    async def test_missing_content_still_falls_back(self, name, cls):
        """Provider 正常但无 content → 仍回落模板（防御路径保留）"""
        agent = cls(provider=_OkProvider())
        result = await agent.execute("分析", _session())
        assert not result.get("error"), f"{name} 不应报错: {str(result)[:120]}"
        assert result  # 返回了模板数据


class _ProbeAgent(BaseAgent):
    """最小可实例化 Agent（用于直接验证 _content_or_error）"""

    meta_name = "探针"

    async def _execute_impl(self, task_brief, session):
        return {}


class TestContentOrErrorHelper:
    def _probe(self):
        return _ProbeAgent(provider=_OkProvider())

    def test_error_wins_over_fallback(self):
        assert self._probe()._content_or_error({"error": "boom"}, {"category": "模板"}) == {"error": "boom"}

    def test_content_used_when_present(self):
        payload = {"category": "化妆品"}
        assert self._probe()._content_or_error({"content": payload}, {"category": "模板"}) is payload

    def test_empty_content_is_kept(self):
        """空 dict 是合法业务结果（不是缺失），不应被模板覆盖"""
        assert self._probe()._content_or_error({"content": {}}, {"category": "模板"}) == {}

    def test_fallback_only_when_content_absent(self):
        probe = self._probe()
        assert probe._content_or_error({"tokens_used": 1}, {"category": "模板"}) == {"category": "模板"}
        # 用量被留存用于记账（成本链路修复的配套）
        assert probe._last_usage["tokens_used"] == 1

    def test_non_dict_result_is_tolerated(self):
        assert self._probe()._content_or_error(None, {"category": "模板"}) == {"category": "模板"}
