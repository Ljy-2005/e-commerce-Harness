"""品类专项分析员 — 按品类深度分析（保健品/化妆品/食品/3C）"""

from src.agents.base import BaseAgent


class CategorySpecialistAgent(BaseAgent):
    """针对特定品类的深度分析"""

    meta_name = "品类专项分析员"
    timeout_ms = 30_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        # 从之前的分析结果中获取品类
        artifacts = session.get("artifacts", {})
        analysis = artifacts.get("analysis", {})
        category = analysis.get("category", "")

        # Mock 模式：直接返回模板数据，绕过 LLM 关键词匹配
        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_by_category(category)

        system_prompt = self._prompt_for_category(category)
        result = await self.provider.chat_with_vision(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task_brief},
            ],
        )
        return result.get("content", self._mock_by_category(category))

    def _mock_by_category(self, category: str) -> dict:
        from src.providers.mock import MOCK_ANALYSIS
        result = dict(MOCK_ANALYSIS)

        if "保健" in category:
            result["marketing_angles"]["marketing_angles"].extend([
                "送礼场景", "职场健康", "家庭常备",
            ])
        elif "化妆" in category or "护肤" in category:
            result["marketing_angles"]["marketing_angles"].extend([
                "成分党种草", "日间/夜间护肤流程", "肤质适配推荐",
            ])
            result["marketing_angles"]["scene_suggestions"].extend([
                "浴室梳妆台", "自然光源特写",
            ])
        elif "食品" in category:
            result["marketing_angles"]["marketing_angles"].extend([
                "食材溯源", "健康零食", "家庭分享",
            ])
        elif "3C" in category.lower() or "数码" in category or "电子" in category:
            result["marketing_angles"]["marketing_angles"].extend([
                "功能对比", "极简设计", "使用场景展示",
            ])

        return result

    def _prompt_for_category(self, category: str) -> str:
        base = "你是电商品类分析专家。"
        if "保健" in category:
            return base + " 重点关注：蓝帽合规、功效宣称边界、目标人群细分（熬夜/饮酒/中老年）、成分差异化。"
        if "化妆" in category or "护肤" in category:
            return base + " 重点关注：质地/色号/肤质适配、成分功效、使用场景（日间/夜间）、季节性。"
        if "食品" in category:
            return base + " 重点关注：配料表、营养宣称、包装展示、食欲感呈现。"
        return base + " 请基于品类特点进行深度分析，补充通用分析可能遗漏的维度。"
