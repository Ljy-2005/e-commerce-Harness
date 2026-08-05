"""合规审查员 — 广告法 + 平台规范检查"""

from src.agents.base import BaseAgent


class ComplianceAgent(BaseAgent):
    """使用 Vision 能力，检查图片的合规性"""

    meta_name = "合规审查员"
    timeout_ms = 30_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        analysis = artifacts.get("analysis", {})
        category = analysis.get("category", "")

        from src.providers.mock import MockLLMProvider
        if self.provider is None or isinstance(self.provider, MockLLMProvider):
            return self._mock_compliance()

        category_rules = self._rules_for_category(category)
        # 用 replace 代替 f-string，避免 category_rules 中的 { } 被误解析
        system_prompt = """你是电商合规审查专家，精通《广告法》《食品安全法》及各电商平台规范。
{category_rules}

审查输出 JSON 格式：
{{
  "passed": true/false,
  "risk_level": "low"/"medium"/"high",
  "violations": ["违规项"],
  "warnings": ["风险提示"],
  "suggestions": ["改进建议"]
}}""".replace("{category_rules}", category_rules)

        images = artifacts.get("images", [])
        user_content = [{"type": "text", "text": task_brief}]
        for img in images[:2]:
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
        return result.get("content", self._mock_compliance())

    def _mock_compliance(self) -> dict:
        from src.providers.mock import MOCK_COMPLIANCE
        return dict(MOCK_COMPLIANCE)

    def _rules_for_category(self, category: str) -> str:
        rules = {
            "保健品": """## 保健品专项规则
1. 必须标注 '保健食品不是药品，不能替代药物治疗'
2. 蓝帽标志必须清晰可见
3. 禁止使用 '治疗''治愈' 等医疗术语
4. 不得出现医生/患者形象推荐
5. 功效宣称必须有蓝帽批文支撑
6. 警示语字体不得小于 5mm""",
            "化妆品": """## 化妆品专项规则
1. 禁止宣称医疗功效
2. 特殊用途化妆品须标注批准文号
3. 不得使用 '药妆''医学护肤' 等表述""",
        }
        return rules.get(category, "## 通用规则\n1. 图片内容不得违反广告法\n2. 文字信息必须真实准确\n3. 不得含有虚假或误导性内容")
