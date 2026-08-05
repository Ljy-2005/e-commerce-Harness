"""审查员 — 5 维度质量评分"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml


class ReviewerAgent(BaseAgent):
    """使用 Vision 能力，按 5 维度审查生成图片"""

    meta_name = "审查员"
    timeout_ms = 30_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        images = artifacts.get("images", [])
        analysis = artifacts.get("analysis", {})

        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_review()

        system_prompt = self._load_prompt("config/prompts/reviewer.yaml")
        user_content = [{"type": "text", "text": f"""请审查以下生成图片。

## 原始商品分析
{analysis}

## 审查要求
{task_brief}"""}]

        for img in images[:3]:
            b64 = img.get("base64_data", "")
            if b64 and len(b64) > 100:
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })

        result = await self.provider.chat_with_vision(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        review = result.get("content", self._mock_review())
        review["iteration"] = session.get("turn_count", 0)
        return review

    def _mock_review(self) -> dict:
        from src.providers.mock import MOCK_REVIEW
        return dict(MOCK_REVIEW)

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", "你是专业的电商图片审查专家。")
        except Exception:
            return "你是专业的电商图片审查专家。按 5 维度（质感/光影/构图/商品还原度/平台适配）评分。"
