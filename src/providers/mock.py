"""Mock Provider — 无 API Key 时自动降级，返回保健品模板数据"""

from src.providers.base import BaseLLMProvider, BaseImageProvider


# ── 模板数据 ──

MOCK_ANALYSIS = {
    "category": "保健品",
    "sub_category": "护肝类",
    "dosage_form": "胶囊",
    "ingredients": ["水飞蓟提取物", "五味子", "蒲公英根"],
    "features": ["蓝帽认证", "高纯度水飞蓟 80%", "每日 2 粒", "植物胶囊壳"],
    "target_audience": {
        "age": "25-50岁",
        "gender": "男女均可",
        "lifestyle": "熬夜加班、饮酒应酬、作息不规律",
        "concerns": "肝脏健康、解酒护肝、转氨酶偏高",
    },
    "style_constraints": {
        "色调": "清新绿色 + 白色，突出植物天然感",
        "光影": "柔和自然光，高亮产品标签",
        "构图": "产品居中，植物原料环绕",
    },
    "special_constraints": [
        "必须标注'保健食品不是药品，不能替代药物治疗'",
        "蓝帽标志必须清晰可见",
        "不可出现'治疗''治愈'等医疗术语",
        "不可使用医生/患者形象做推荐",
    ],
    "marketing_angles": {
        "selling_points": ["高纯度水飞蓟 80%", "蓝帽认证", "植物配方"],
        "marketing_angles": ["熬夜护肝", "饮酒应酬必备", "职场人肝脏健康"],
        "copy_suggestions": ["为你的肝脏撑把保护伞", "熬夜不怕，有备而来"],
        "emotional_appeal": "健康焦虑 + 职场压力",
        "scene_suggestions": ["办公桌", "实验室科研场景", "送礼礼盒装"],
        "seasonal_tags": ["年中大促", "双十一", "健康好礼"],
        "regulatory_red_lines": [
            "禁止使用'治疗''治愈'等医疗术语",
            "不得暗示可以替代药物治疗",
            "功效宣称必须有蓝帽批文支撑",
        ],
    },
    "compliance_notes": [
        "蓝帽标志位置合规",
        "'保健食品不是药品' 警示语字体不小于 5mm",
    ],
    "confidence_score": 85.0,
}

MOCK_PROMPTS = {
    "main_image": {
        "prompt": "护肝胶囊产品，白底，水飞蓟植物元素环绕，柔和自然光，高清晰度，商业摄影质感 --style 电商白底主图",
        "negative_prompt": "模糊，暗沉，文字，水印，人物，阴影过重",
        "style": "白底商业摄影",
        "composition": "产品居中，植物环绕底部",
    },
    "scene_images": [
        {
            "prompt": "护肝胶囊置于办公桌上，旁有咖啡杯和笔记本电脑，清晨阳光洒入，健康职场氛围 --style 生活方式场景",
            "scene_type": "办公桌场景",
            "style": "生活方式摄影",
            "platform": "taobao",
        },
        {
            "prompt": "护肝胶囊礼盒装，丝带环绕，木纹桌面，高级感送礼场景 --style 送礼场景",
            "scene_type": "送礼场景",
            "style": "高级静物摄影",
            "platform": "taobao",
        },
    ],
    "social_images": [
        {
            "prompt": "护肝胶囊 + 水飞蓟植物 + 文字'熬夜护肝'，小红书种草风，明亮暖色调，生活方式感 --style 小红书种草",
            "scene_type": "种草图文",
            "style": "小红书种草风",
            "platform": "xiaohongshu",
        },
    ],
    "model_variants": {
        "dalle": "A professional e-commerce product photo of milk thistle liver support capsules, white background, soft natural lighting, silk thistle botanical elements surrounding the bottle. Clean commercial photography.",
        "seedream": "护肝胶囊电商主图，白底，水飞蓟植物环绕，柔和自然光，超高清晰度，商业摄影",
        "flux": "Professional product photography of liver support supplement capsules, pristine white background, milk thistle herbs botanicals, soft diffused studio lighting, commercial e-commerce style",
        "midjourney": "Liver support capsules product photography, white background, botanical milk thistle elements, soft lighting, commercial quality --style raw --ar 1:1",
    },
}

MOCK_IMAGE = {
    "image_url": "",
    "base64_data": "",  # 空 -> 由 MockImageProvider 生成 SVG 占位图
    "model_used": "mock/svg-placeholder",
    "generation_params": {"size": "1024x1024"},
}

MOCK_REVIEW = {
    "overall_score": 82.0,
    "dimension_scores": {
        "texture": 85.0,
        "lighting": 80.0,
        "composition": 82.0,
        "product_fidelity": 88.0,
        "platform_fit": 75.0,
    },
    "top_issues": ["背景不够纯净，有轻微色偏", "社交图缺少品牌 Logo"],
    "top_praises": ["产品质感表现优秀", "植物元素搭配自然", "合规标注完整"],
    "verdict": "pass",
    "needs_human_review": False,
    "iteration": 0,
}

MOCK_COMPLIANCE = {
    "passed": True,
    "risk_level": "low",
    "violations": [],
    "warnings": ["建议将警示语字体适当放大"],
    "suggestions": ["蓝帽标志位置合规", "文字排版符合淘宝平台规范"],
}

MOCK_COORDINATOR_WORKFLOW = [
    {"action": "invite", "agent_name": "商品分析员", "task_brief": "分析上传的商品图片，识别品类、材质、卖点、目标人群和风格约束"},
    {"action": "invite", "agent_name": "品类专项分析员", "task_brief": "针对保健品品类进行深度分析，重点关注合规维度和营销角度"},
    {"action": "invite", "agent_name": "提示词生成员", "task_brief": "基于分析结果，生成淘宝平台的完整提示词（主图+场景图+社交图）"},
    {"action": "invite", "agent_name": "生图员", "task_brief": "使用主图提示词生成 3 张商品图片"},
    {"action": "invite", "agent_name": "图像后处理员", "task_brief": "对生成图片进行去背景处理"},
    {"action": "invite", "agent_name": "审查员", "task_brief": "按 5 维度审查生成图片的质量"},
    {"action": "invite", "agent_name": "合规审查员", "task_brief": "检查图片是否符合广告法和淘宝平台规范"},
    {"action": "done", "agent_name": "", "task_brief": "所有产出物就绪"},
]

# ── Mock Provider 实现 ──


class MockLLMProvider(BaseLLMProvider):
    """Mock LLM — 基于中文关键词返回匹配的模板数据"""

    name = "mock"
    capabilities = ["vision", "text"]

    async def chat(self, messages: list[dict], model: str = "", json_mode: bool = False) -> dict:
        content = self._infer_content_from_messages(messages)
        return {"content": content, "tokens_used": 50, "cost_usd": 0.0}

    async def chat_with_vision(self, messages: list[dict], model: str = "") -> dict:
        content = self._infer_content_from_messages(messages)
        return {"content": content, "tokens_used": 100, "cost_usd": 0.0}

    def _infer_content_from_messages(self, messages: list[dict]) -> dict:
        """根据消息内容中的关键词推断应返回的模板"""
        combined = " ".join(
            str(m.get("content", "")) for m in messages
        )

        # Coordinator → 返回 workflow 决策序列
        if "已注册 Agent" in combined or "决策" in combined or "coordinator" in combined.lower():
            return MOCK_COORDINATOR_WORKFLOW[self._coordinator_step(combined)]

        # 审查/质检
        if any(w in combined for w in ["审查", "质检", "评分", "审查员", "review"]):
            return MOCK_REVIEW

        # 合规
        if any(w in combined for w in ["合规", "广告法", "规范"]):
            return MOCK_COMPLIANCE

        # 提示词
        if any(w in combined for w in ["提示词", "prompt"]):
            return MOCK_PROMPTS

        # 分析
        if any(w in combined for w in ["分析", "品类", "卖点", "成分", "材质"]):
            return MOCK_ANALYSIS

        # 默认：返回分析结果
        return MOCK_ANALYSIS

    def _coordinator_step(self, combined: str) -> int:
        """根据消息中的 turn 信息返回 workflow 第几步"""
        import re
        # 简单策略：统计 "邀请" 关键词出现次数
        count = len(re.findall(r"邀请", combined))
        return min(count, len(MOCK_COORDINATOR_WORKFLOW) - 1)


class MockImageProvider(BaseImageProvider):
    """Mock Image — 返回 SVG 占位图"""

    name = "mock"
    capabilities = ["image"]

    async def generate(
        self, prompt: str, negative_prompt: str = "", size: str = "1024x1024", model: str = ""
    ) -> dict:
        import base64
        svg = self._make_placeholder(prompt[:50], size)
        b64 = base64.b64encode(svg.encode()).decode()
        return {
            "image_url": f"data:image/svg+xml;base64,{b64}",
            "base64_data": b64,
            "cost_usd": 0.0,
        }

    def _make_placeholder(self, label: str, size: str) -> str:
        w, h = 800, 800
        if "x" in size:
            parts = size.split("x")
            w, h = int(parts[0]), int(parts[1])
        return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">
  <rect width="{w}" height="{h}" fill="#f0f4e8"/>
  <text x="{w/2}" y="{h/2-20}" text-anchor="middle" font-family="sans-serif" font-size="18" fill="#5a7a3a">
    🛒 Mock 生图
  </text>
  <text x="{w/2}" y="{h/2+15}" text-anchor="middle" font-family="sans-serif" font-size="12" fill="#888">
    {label}
  </text>
</svg>"""
