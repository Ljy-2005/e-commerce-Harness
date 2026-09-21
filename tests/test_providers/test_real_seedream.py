"""Seedream 真实生图测试（test-plan P4 L6，@pytest.mark.real）

- 默认套件不跑（addopts `-m 'not real and not slow'`）；
- 缺失火山 AK/SK 与即梦 Key 自动 skip；
- 真实生成 1 张（1024x1024）会消耗即梦/火山额度——仅在用户批准时手动触发。
"""

import os

import pytest

import src.core.config  # noqa: F401  触发 secrets.yaml → env 注入
from src.providers.seedream import SeedreamImageProvider

_HAS_KEY = bool(
    os.getenv("SEEDREAM_API_KEY")
    or (os.getenv("VOLCANO_ACCESS_KEY") and os.getenv("VOLCANO_SECRET_KEY"))
)

pytestmark = [
    pytest.mark.real,
    pytest.mark.skipif(not _HAS_KEY, reason="缺少 SEEDREAM_API_KEY 或火山 AK/SK（真实套件跳过）"),
]


class TestRealGenerate:
    @pytest.mark.asyncio
    async def test_generate_returns_image(self):
        """真实生成 1 张：断言 image_url/base64 有效 + 成本字段"""
        result = await SeedreamImageProvider().generate(
            prompt="白色背景的保健品护肝片产品主图，电商风格，高清",
            size="1024x1024",
        )
        assert "error" not in result, result
        assert result.get("image_url") or result.get("base64_data"), "生成结果既无 image_url 也无 base64_data"
        assert result.get("model_used") == "seedream-5.0"
        assert result.get("cost_usd", 0) >= 0
