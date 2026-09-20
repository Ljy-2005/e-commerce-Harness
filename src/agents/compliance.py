"""合规审查员 — 广告法 + 平台规范检查"""

from src.agents.base import BaseAgent


class ComplianceAgent(BaseAgent):
    """使用 Vision 能力，检查图片的合规性"""

    meta_name = "合规审查员"
    timeout_ms = 150_000

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

        # A31：与审查员共用图源解析（真实 Provider 只回 URL，此前完全送不进去）。
        # 本轮：把**用户上传的真实商品图**一起送进去（图一），合规审查才有比对基准 ——
        # 臆造品牌/认证这类问题（实测把 DEFOEBUENA® 编成 NUTRIVA®）只有对着原图才判得准。
        from src.harness.vision_payload import image_parts, reference_image_parts
        ref_parts, ref_notes, _ = reference_image_parts(session, limit=1)
        parts, notes = await image_parts(images, limit=2)
        if not parts:
            reason = "；".join(notes) or "未找到任何可用的生成图"
            return {
                "error": f"NO_IMAGE_ACCESSIBLE: {reason}",
                "passed": False,
                "risk_level": "high",
                "violations": ["缺少可审查的图像"],
                "warnings": [f"图像不可用：{reason}"],
                "suggestions": ["重新生成图片后再做合规审查"],
            }
        header = task_brief
        if ref_parts:
            header += ("\n\n## 图像说明\n图一 = 用户上传的真实商品图（基准）；"
                       "其后为本次生成的图。发现生成图里出现图一中不存在的品牌、认证或"
                       "功效文字时，必须按合规风险上报。")
        all_notes = ref_notes + notes
        if all_notes:
            header += "\n\n## 图像获取说明\n" + "\n".join(f"- {n}" for n in all_notes)
        user_content[0] = {"type": "text", "text": header}
        user_content.extend(ref_parts)
        user_content.extend(parts)

        result = await self.provider.chat_with_vision(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            **self._model_kwargs(),
        )
        return self._content_or_error(result, self._mock_compliance())

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
