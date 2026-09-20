"""商品分析员 — 视觉分析商品图片

用户反馈（本轮）："产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者
提醒，这是一个很大的缺失"。核实：这里的提示词 schema 只有品类/成分/卖点，没有品牌、没有
商品名、也没有任何包装文字转录 —— 下游于是全程没有事实基准，实测把 `DEFOEBUENA®` 编成了
`NUTRIVA®`。

现在：
- 分析结果必须带 `product_identity`（品牌/品名/规格/认证 + 识别依据 + 来源）与
  `visible_text`（包装文字逐字转录），由 `harness/product_identity.py` 规范化成"身份卡"；
- 上传图的 data URI **按魔数嗅探 MIME**（实测上传的是 PNG，此前写死 image/jpeg）；
- Mock 或"Provider 正常但没给 content"→ 显式标记 `is_mock` 且身份永远 `uncertain`，
  **不许冒充本次商品的真实分析**（用户担心"套成别的牌子"）。
"""

from src.agents.base import BaseAgent
from src.core.config import load_yaml
from src.harness.product_identity import normalize_identity
from src.harness.vision_payload import inline_data_uri


class ProductAnalystAgent(BaseAgent):
    """使用 Vision 能力分析商品图片，输出 ProductAnalysis"""

    meta_name = "商品分析员"
    timeout_ms = 150_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        task = session.get("task", {})
        images = task.get("product_images", [])

        from src.providers.mock import MockLLMProvider
        is_mock_provider = self.provider is None or isinstance(self.provider, MockLLMProvider)

        # 构建 prompt
        system_prompt = self._load_prompt("config/prompts/analyst.yaml")
        user_content = [{"type": "text", "text": task_brief}]
        for img in images[:5]:  # 最多 5 张
            uri = inline_data_uri(img)
            if uri:
                user_content.append({"type": "image_url", "image_url": {"url": uri}})

        if is_mock_provider:
            return self._demo_analysis()

        result = await self.provider.chat_with_vision(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            **self._model_kwargs(),
        )
        # 不直接回落 mock：Provider 正常但没 content 时，宁可显式标成演示数据
        content = self._content_or_error(result, {})
        if isinstance(content, dict) and content.get("error"):
            return content
        if isinstance(content, str):
            # 自定义兼容端点可能不走 json_mode：这里兜一层，避免把一段裸文本
            # 当成"结构化分析结果"流到下游（那会让协调者/提示词读到一坨散文）
            from src.providers.json_parse import parse_json_loose
            parsed = parse_json_loose(content)
            content = parsed if isinstance(parsed, dict) else {"raw": content}
        if not content:
            return self._demo_analysis()

        content = dict(content)
        return self._attach_identity(content, source="vision")

    def _attach_identity(self, content: dict, *, source: str) -> dict:
        """把身份卡写进分析结果（`source` 决定它算不算"已确认"）"""
        identity = normalize_identity(content, source=source)
        content["product_identity"] = identity
        content["identity_status"] = identity["status"]
        return content

    def _demo_analysis(self) -> dict:
        """演示数据（Mock / Provider 没给内容时）：中性 + 显式标记，绝不冒充事实"""
        content = dict(self._mock_analysis())
        content = self._attach_identity(content, source="mock")
        content["is_mock"] = True
        content["demo_notice"] = "⚠️ 演示数据（未接入真实视觉分析），非本次商品的事实"
        return content

    def _mock_analysis(self) -> dict:
        from src.providers.mock import MOCK_ANALYSIS
        return dict(MOCK_ANALYSIS)

    def _load_prompt(self, path: str) -> str:
        try:
            cfg = load_yaml(path)
            return cfg.get("system", "你是一个专业的电商商品分析专家。")
        except Exception:
            return "你是一个专业的电商商品分析专家。请分析商品图片，输出结构化分析结果。"
