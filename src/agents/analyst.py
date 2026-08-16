"""商品分析员 — 视觉分析商品图片"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml


class ProductAnalystAgent(BaseAgent):
    """使用 Vision 能力分析商品图片，输出 ProductAnalysis"""

    meta_name = "商品分析员"
    timeout_ms = 30_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        task = session.get("task", {})
        images = task.get("product_images", [])

        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_analysis()

        # 构建 prompt
        system_prompt = self._load_prompt("config/prompts/analyst.yaml")
        user_content = [{"type": "text", "text": task_brief}]
        for img in images[:5]:  # 最多 5 张
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img}"},
            })

        result = await self.provider.chat_with_vision(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            **self._model_kwargs(),
        )
        return result.get("content", self._mock_analysis())

    def _mock_analysis(self) -> dict:
        from src.providers.mock import MOCK_ANALYSIS
        return MOCK_ANALYSIS

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", "你是一个专业的电商商品分析专家。")
        except Exception:
            return "你是一个专业的电商商品分析专家。请分析商品图片，输出结构化分析结果。"
