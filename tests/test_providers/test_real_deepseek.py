"""DeepSeek V4 真实 API 测试（test-plan P4 L6，@pytest.mark.real）

- 默认套件不跑：pyproject addopts `-m 'not real and not slow'`；
- 缺失 Key 自动 skip（Key 由 config/secrets.yaml 在导入时注入或环境变量提供）；
- 本地运行：`python -m pytest -m real tests/test_providers/test_real_deepseek.py -v`
- CI：`.github/workflows/real-api.yml`（manual workflow，用仓库 secrets）

⚠️ 真实调用会产生极小费用（本套件用例为最小输入：1 次 chat / 1 次视觉读图 / 1 次 401）。
"""

import base64
import io
import os

import pytest
from PIL import Image, ImageDraw

import src.core.config  # noqa: F401  触发 secrets.yaml → env 注入（Key 缺失时套件 skip）
from src.providers.deepseek import DeepSeekLLMProvider

pytestmark = [
    pytest.mark.real,
    pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="缺少 DEEPSEEK_API_KEY（真实套件跳过）"),
]

MODEL_TEXT = "deepseek-v4-flash"
MODEL_VISION = "deepseek-v4-flash-vision-exp"


def _provider() -> DeepSeekLLMProvider:
    return DeepSeekLLMProvider()


def _pattern_image_base64(size: int = 64) -> str:
    """白色底 + 红色圆 + 蓝色方块（给视觉模型可识别的确定图案）"""
    img = Image.new("RGB", (size, size), "white")
    d = ImageDraw.Draw(img)
    d.ellipse([size // 4, size // 4, size * 3 // 4, size * 3 // 4], fill="red")
    d.rectangle([0, 0, size // 4, size // 4], fill="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class TestRealChat:
    @pytest.mark.asyncio
    async def test_chat_returns_parsed_content(self):
        """真实 chat 调用：返回解析后的 content + token/成本字段"""
        result = await _provider().chat(
            [{"role": "user", "content": "用一句话回答：1+1 等于几？"}],
            model=MODEL_TEXT,
        )
        assert "error" not in result, result
        content = result["content"]
        assert isinstance(content, dict) and content.get("text")
        assert result["tokens_used"] > 0
        assert result["cost_usd"] >= 0

    @pytest.mark.asyncio
    async def test_chat_json_mode_returns_dict(self):
        """json_mode=True：response_format=json_object → content 是 dict"""
        result = await _provider().chat(
            [{"role": "user", "content": '请以 JSON 格式回答，输出 {"answer": 42}'}],
            model=MODEL_TEXT,
            json_mode=True,
        )
        assert "error" not in result, result
        assert isinstance(result["content"], dict)
        assert "answer" in result["content"]

    @pytest.mark.asyncio
    async def test_invalid_key_returns_401(self, monkeypatch):
        """错误 Key → 401 错误 dict（不抛异常、不计费）"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-invalid-real-test-key")
        result = await _provider().chat(
            [{"role": "user", "content": "hi"}],
            model=MODEL_TEXT,
        )
        assert "error" in result
        assert "401" in result["error"]


class TestRealVision:
    @pytest.mark.asyncio
    async def test_chat_with_vision_reads_image(self):
        """vision-exp 真实多模态读图：确定图案（红圆蓝块）被识别"""
        b64 = _pattern_image_base64()
        result = await _provider().chat_with_vision([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "描述这张图片里的形状和颜色，简短回答"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ], model=MODEL_VISION)
        assert "error" not in result, result
        content = result["content"]
        text = content.get("text") if isinstance(content, dict) else str(content)
        assert text, "视觉模型未返回任何文本"
        # 图案包含红圆 + 蓝块：能识别出颜色/形状即证明多模态链路真实有效
        assert any(kw in text for kw in ("红", "圆", "red", "circle", "蓝", "blue")), f"识别结果异常: {text[:120]}"
        assert result["tokens_used"] > 0


class TestRealHarnessIntegration:
    """真实调用走完整 harness 保护链（熔断/限流/重试/成本追踪），
    对应 test-plan L6「成本与超时：真实调用计入 cost_tracker；超时路径不误熔断」"""

    @pytest.mark.asyncio
    async def test_real_call_tracks_cost_and_keeps_circuit_closed(self):
        from src.agents.base import BaseAgent, get_circuit_breaker

        class _ProbeAgent(BaseAgent):
            meta_name = "真实调用探针"
            timeout_ms = 60_000

            async def _execute_impl(self, task_brief: str, session) -> dict:
                return await self.provider.chat(
                    [{"role": "user", "content": task_brief}],
                    model=MODEL_TEXT,
                )

        agent = _ProbeAgent(provider=_provider())
        session = {
            "tenant_id": "default",
            "task": {"platform": "taobao"},
            "cost_so_far": 0.0,
            "cost_budget_usd": 10.0,
        }
        result = await agent.execute("用一句话回答：中国的首都是哪里？", session)
        assert "error" not in result, result
        assert result["tokens_used"] > 0
        # 真实调用计入 cost_tracker（session.cost_so_far 已更新）
        assert session["cost_so_far"] > 0
        # 成功调用不误熔断
        cb = get_circuit_breaker("deepseek")
        assert cb.state.value == "closed"
