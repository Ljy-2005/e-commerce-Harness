"""风格拆解员 — 拆解参考图的构图/光影/色调/元素/风格标签

M3 一键风格复刻的核心 Agent：通过 config/agents/style_analyst.yaml 注册，
注册中心按 `class` 字段动态加载（插件化，无需改动核心代码）。
"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml


class StyleAnalystAgent(BaseAgent):
    """使用 Vision 能力拆解参考图的风格要素"""

    meta_name = "风格拆解员"
    timeout_ms = 30_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_breakdown()

        task = session.get("task", {})
        reference_images = task.get("reference_images", [])
        user_content = [{"type": "text", "text": task_brief or "请拆解参考图的风格要素"}]
        for img in reference_images[:3]:
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img}"},
            })

        result = await self.provider.chat_with_vision(
            messages=[
                {"role": "system", "content": self._load_prompt("config/prompts/style_analyst.yaml")},
                {"role": "user", "content": user_content},
            ],
            **self._model_kwargs(),
        )
        return result.get("content", self._mock_breakdown())

    def _mock_breakdown(self) -> dict:
        """Mock：返回模板风格拆解（含可直接拼入提示词的文本描述）"""
        return {
            "composition": "主体居中偏右，四周留白呼吸感强",
            "lighting": "柔和高调布光，无明显硬阴影",
            "color_palette": ["米白", "浅灰", "草木绿"],
            "elements": ["植物元素环绕", "纯净背景", "局部特写"],
            "style_tags": ["简约", "天然", "高级感"],
            "style_prompt_text": (
                "构图主体居中偏右、四周留白呼吸感强；柔和高调布光、无明显硬阴影；"
                "色调以米白/浅灰/草木绿为主；植物元素环绕、纯净背景、局部特写；"
                "整体风格简约、天然、高级感"
            ),
        }

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", "你是专业的视觉风格拆解专家。")
        except Exception:
            return "你是专业的视觉风格拆解专家。请拆解参考图的构图、光影、色调、元素与风格标签。"
