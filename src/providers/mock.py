"""Mock Provider — 无 API Key 时自动降级，返回**中性**演示数据

## 为什么是"中性"（用户实测质疑："这不会直接影响到我生产其他牌子的商品吗"）

此前的 `MOCK_ANALYSIS` 写死了整套具体商品事实（`保健品 / 护肝类 / 水飞蓟提取物 /
蓝帽认证 / 25-50 岁 / 解酒护肝`）。而 `ProviderRegistry.resolve()` 的兜底是**静默回落 mock**，
`base.py::_content_or_error` 也有一条回落通道 —— 于是视觉服务不可用（Key 失效/模型下架/
额度耗尽）时，用户那款商品会被套上"护肝胶囊、水飞蓟"的分析事实，界面还显示成功，
下游提示词/生图/审查全程当事实用。

现在模板里**不含任何商品事实**（品牌/品名/成分/认证一律留空），并显式带
`is_mock: true` + `product_identity.source = "mock"` → 身份卡永远 `uncertain`，
群聊与产物会标注"⚠️ 演示数据，非本次商品"。字段名保持不变，减少测试与前端改动。
"""

from src.providers.base import BaseLLMProvider, BaseImageProvider


# ── 模板数据 ──

MOCK_ANALYSIS = {
    "is_mock": True,
    # 具体商品事实一律留空：演示数据不得冒充"本次上传商品"的分析结论
    "product_identity": {
        "brand": "",
        "product_name": "",
        "spec": "",
        "certifications": [],
        "package_form": "",
        # 演示数据不得冒充"包装上取到的品牌色"（否则画面配色会被套成演示配色）
        "brand_palette": {"primary": "", "secondary": "", "background": "",
                          "evidence": "演示数据，未做真实取样"},
        "confidence": 0.0,
        "evidence": "演示数据，未做真实视觉识别",
        "source": "mock",
    },
    "visible_text": {"lines": [], "language": "", "has_illegible": False},
    "category": "未识别（演示数据）",
    "sub_category": "",
    "dosage_form": "",
    # 演示用的**占位文案**（不是任何真实商品的事实）：让"信息图"链路在 Mock 下也能跑通
    # —— 这些字会被本地排版画进图里，且句句自带"演示"字样，不会被误当成真实卖点/成分
    "ingredients": ["演示成分甲（非真实商品資訊）", "演示成分乙（非真实商品資訊）"],
    "features": ["演示賣點一（非真实商品資訊）", "演示賣點二（非真实商品資訊）"],
    "target_audience": {
        "age": "演示人群：25–45 歲",
        "gender": "演示人群：男女皆可",
        "lifestyle": "演示人群：都市上班族",
        "concerns": "演示人群：日常保養",
    },
    "style_constraints": {
        "色调": "以平台规范为准",
        "光影": "柔和均匀布光",
        "构图": "主体居中，占比 85% 以上",
    },
    "special_constraints": [
        "演示数据：以上内容不是对本次上传商品的真实分析，请勿据此生成品牌/文字",
    ],
    "marketing_angles": {
        "selling_points": ["演示賣點一（非真实商品資訊）", "演示賣點二（非真实商品資訊）"],
        "marketing_angles": [],
        "copy_suggestions": [],
        "emotional_appeal": "",
        "scene_suggestions": ["演示場景：辦公桌", "演示場景：居家餐桌"],
        "regulatory_red_lines": [
            "禁止使用'治疗''治愈'等医疗术语",
            "不得暗示可以替代药物治疗",
        ],
    },
    "compliance_notes": [],
    "confidence_score": 0.0,
}

MOCK_PROMPTS = {
    "is_mock": True,
    # 演示用的套图编排（固定 5 个槽位：2 张纯摄影 + 3 张信息图；真实运行由提示词生成员
    # 按 config/platforms.yaml 的平台槽位生成）。**不含任何商品事实**，
    # 也**不要求画面里出现文字** —— 信息图的文字由本地排版引擎绘制（演示占位文案）。
    "set_plan": {
        "platform": "",
        "slots": [
            {
                "slot_id": "main_white",
                "role": "纯商品图",
                "prompt": "商品本体正面平视，纯白背景 #FFFFFF，柔和均匀商业摄影布光，"
                          "主体居中占比 85% 以上，画面干净无文字无水印无道具",
                "negative_prompt": "模糊，暗沉，文字，水印，人物，阴影过重",
                "composition": "主体居中，正面平视，占比 85% 以上",
                "background": "#FFFFFF",
                "text_in_image": False,
                "uses_reference": ["upload_1"],
                "aspect": "1:1",
                "notes": "演示数据（Mock）",
            },
            {
                "slot_id": "main_scene",
                "role": "使用场景图",
                "prompt": "商品本体置于简洁生活场景中（原木桌面，清晨自然光斜射），"
                          "环境干净克制，浅景深突出主体，画面内不出现任何文字",
                "negative_prompt": "文字，水印，杂乱背景",
                "composition": "主体居中偏下，环境交代充足",
                "background": "",
                "text_in_image": False,
                "uses_reference": ["upload_1"],
                "aspect": "1:1",
                "notes": "演示数据（Mock）",
            },
            {
                # 信息图：模型只出**无字底图 + 留白版式**，文字（演示占位文案）由本地排版绘制
                "slot_id": "main_selling_point",
                "role": "核心卖点图",
                "prompt": "商品本体置于画面右下角，左上与上方留出大面积纯净白色版式区域"
                          "（供系统排版文字），纯白背景，浅景深，柔和光影，画面内不出现任何文字",
                "negative_prompt": "文字，水印，杂乱",
                "composition": "商品居右下，左上留白",
                "background": "#FFFFFF",
                "text_in_image": False,
                "uses_reference": ["upload_1"],
                "aspect": "1:1",
                "notes": "演示数据（Mock）",
            },
            {
                "slot_id": "main_benefits",
                "role": "功效列举图",
                "prompt": "极简版式底图：纯白背景 + 上部浅色标题条区域 + 中部网格留白，"
                          "商品本体缩小于右下角，画面内不出现任何文字",
                "negative_prompt": "文字，水印，杂乱",
                "composition": "版式留白为主，商品在角落",
                "background": "#FFFFFF",
                "text_in_image": False,
                "uses_reference": ["upload_1"],
                "aspect": "1:1",
                "notes": "演示数据（Mock）",
            },
            {
                "slot_id": "main_audience",
                "role": "适用人群图",
                "prompt": "生活化版式底图：柔和浅色背景 + 左侧人物剪影氛围 + 右侧留白区域"
                          "（供系统排版人群信息），画面内不出现任何文字",
                "negative_prompt": "文字，水印，杂乱",
                "composition": "左图右文，右侧留白",
                "background": "",
                "text_in_image": False,
                "uses_reference": ["upload_1"],
                "aspect": "1:1",
                "notes": "演示数据（Mock）",
            },
        ],
    },
    "main_image": {
        "prompt": "商品本体正面平视，纯白背景 #FFFFFF，柔和均匀商业摄影布光，"
                  "主体居中占比 85% 以上，画面干净无文字无水印",
        "negative_prompt": "模糊，暗沉，文字，水印，人物，阴影过重",
        "style": "白底商业摄影（演示数据）",
        "composition": "主体居中，正面平视",
        "slot_id": "main_white",
    },
    "scene_images": [
        {
            "prompt": "商品本体置于原木桌面，清晨自然光斜射，环境简洁克制，"
                      "浅景深突出主体，画面内不出现任何文字",
            "scene_type": "桌面场景",
            "style": "生活方式摄影（演示数据）",
            "platform": "",
        },
        {
            "prompt": "商品本体置于浅灰渐变背景的极简静物台，顶部柔和扩散光，"
                      "投影干净，画面内不出现任何文字",
            "scene_type": "静物场景",
            "style": "极简静物摄影（演示数据）",
            "platform": "",
        },
    ],
    "social_images": [
        {
            "prompt": "商品本体在温暖居家场景中的生活方式画面，暖色调、浅景深、氛围感，"
                      "画面内不出现任何文字（种草文案由后期贴图）",
            "scene_type": "生活方式",
            "style": "生活种草风（演示数据）",
            "platform": "",
        },
    ],
    "model_variants": {
        "dalle": "Professional e-commerce product photo of the product itself, pure white "
                 "background, soft even studio lighting, centered, no text, no watermark",
        "seedream": "商品本体电商主图，纯白背景 #FFFFFF，柔和均匀布光，主体居中，画面无文字无水印",
        "flux": "Professional product photography, pristine white background, soft diffused "
                "studio lighting, centered subject, clean, no text overlay",
        "midjourney": "product photography, white background, soft studio lighting, clean and "
                      "minimal, no text --style raw --ar 1:1",
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

# 提示词审核优化员（Mock 模式）：**不冒充真实审美评价** —— 明确标注"演示数据"，
# 且不带任何 scene 改写稿（否则演示配色/写法会被当成真结论）
MOCK_PROMPT_REVIEW = {
    "is_mock": True,
    "verdict": "pass",
    "overall_score": None,
    "aesthetic_scores": {},
    "slots": [],
    "missing_slots": [],
    "notes": ["⚠️ 演示数据：Mock 模式未做真实审美审核（未接入文本模型）"],
}

# 风格档案员（Mock 模式）：**模板词条**，供「风格词库」在无 Key 时也能走完整链路。
# 不含任何商品事实（品牌/成分/认证/色值），与真实分析结果同一套字段。
MOCK_STYLE_ENTRY = {
    "is_mock": True,
    "name_suggestions": ["冷白留白感", "柔光渐层", "干净商业底"],
    "summary": "大面积留白 + 冷白底 + 短投影，走「干净正规」的第一印象（演示数据）",
    "style_words": "冷白底几乎无缝，主体居中偏左占画面约六成，四周留白充足；"
                   "顶部柔光加双侧补光，投影短而淡；只在主体边缘留一条细窄高光；"
                   "整幅不超过三色系，明度偏高、饱和度偏低。",
    "background": "冷白到极浅灰的竖向渐层（明度差 ≤5%），无可见纹理",
    "composition": "主体居中偏左、占画面 60–70%，右侧留竖向空白，视觉重心略高于几何中心",
    "lighting": "顶部柔光箱为主光 + 双侧补光，投影短椭圆、约 10% 透明度，高光克制",
    "materials": "哑光纸盒纤维可见；若包装本身有镜面工艺，让该区域出现细窄高光",
    "elements": ["一条品牌色细色带", "细线框"],
    "palette_roles": {"primary": "色带与标题条", "secondary": "点缀",
                      "background": "冷白或极浅灰"},
    "whitespace": "主体 60–70%，一侧留白 ≥30%",
    "forbid": ["撞色", "密集光斑", "暗调脏底"],
    "keep_clear_hint": "信息图版式下留白 ≥40% 且集中在左上",
    # 套图结构（**演示值**：Mock 模式没有真实分析，界面上会标明"未做真实分析/未产生费用"）
    "shot_flow": "先白底立信任 → 再讲卖点 → 最后讲配方（演示数据）",
    "shot_roles": [
        {"number": 1, "slot": "main_white", "treatment": "纯白无缝底、无设计元素、主体占约 88%"},
        {"number": 2, "slot": "main_selling_point", "treatment": "左侧与上方留白 ≥45%，商品置右下"},
        {"number": 3, "slot": "main_ingredients", "treatment": "上半部留条目区，商品置于下半部"},
        {"number": 4, "slot": "main_audience", "treatment": "上方留标题条，左侧留三栏说明区"},
    ],
    "taste_verdict": "干净、冷静、留白充足：明度偏高、饱和度偏低，投影几乎不可见，"
                     "只在边缘留一条细窄高光；色系不超过三色。",
    "reward_points": ["留白充足", "明度偏高", "投影极淡", "边缘细窄高光"],
    "avoid_points": ["暖黄调", "大面积饱和色块", "强投影", "密集光斑"],
    "removed_brand_text": [],
    "applies_to": {},
    "as_anchor": True,
}

MOCK_COORDINATOR_WORKFLOW = [    {"action": "invite", "agent_name": "商品分析员", "task_brief": "分析上传的商品图片，识别品类、材质、卖点、目标人群和风格约束"},
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
    # Mock 接受一切（让链路在无 Key 时也能完整跑通），但会把实参如实回显，便于断言
    supports_reference = True
    supported_options = ("watermark", "output_format")
    supports_negative_prompt = True

    async def generate(
        self, prompt: str, negative_prompt: str = "", size: str = "1024x1024", model: str = "",
        *, reference_images: list[str] | None = None, options: dict | None = None,
    ) -> dict:
        import base64
        svg = self._make_placeholder(prompt[:50], size)
        b64 = base64.b64encode(svg.encode()).decode()
        references = [r for r in (reference_images or []) if str(r or "").strip()]
        return {
            "image_url": f"data:image/svg+xml;base64,{b64}",
            "base64_data": b64,
            "cost_usd": 0.0,
            "reference_count": len(references),
            "ignored_params": [],
            "request_params": {"model": model or "mock", "size": size,
                               "image_count": len(references),
                               "prompt_chars": len(prompt or ""),
                               **(options or {})},
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
