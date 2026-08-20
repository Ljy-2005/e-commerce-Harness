"""提示词生成员 — 分析结果 → 各平台各模型生图提示词"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml


class PromptGeneratorAgent(BaseAgent):
    """使用 Text 能力，将分析结果转化为专业生图提示词"""

    meta_name = "提示词生成员"
    timeout_ms = 15_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        analysis = artifacts.get("analysis", {})
        task = session.get("task", {})
        platform = task.get("platform", "taobao")

        # 尝试召回历史成功模板
        recalled = ""
        try:
            from src.harness.agent_memory import AgentMemory
            memory = AgentMemory()
            category = analysis.get("category", "")
            features = analysis.get("features", [])
            similar = await memory.recall_similar(category, features, limit=2,
                                                  tenant_id=session.get("tenant_id", ""))
            if similar:
                recalled = f"\n## 历史成功参考（仅供参考，不要照抄）\n"
                for i, e in enumerate(similar):
                    recalled += f"{i+1}. 评分: {e['score']}/100 | 提示词: {e.get('prompts', {}).get('main', '')[:200]}\n"
        except Exception:
            pass

        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_prompts()

        system_prompt = self._load_prompt("config/prompts/prompt_gen.yaml")
        user_msg = f"""## 商品分析结果
{analysis}

## 任务
{task_brief}

## 目标平台
{platform}

请生成完整的提示词方案（主图 + 场景图 + 社交图 + 多模型版本）。"""

        result = await self.provider.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            json_mode=True,
            **self._model_kwargs(),
        )
        return result.get("content", self._mock_prompts())

    def _mock_prompts(self) -> dict:
        from src.providers.mock import MOCK_PROMPTS
        return MOCK_PROMPTS

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", "你是专业的 AI 图像提示词工程师。")
        except Exception:
            return "你是专业的 AI 图像提示词工程师。请根据商品分析结果生成高质量的生图提示词。"
