"""全部 Pydantic 数据模型"""

import uuid
from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field


def _uid() -> str:
    return uuid.uuid4().hex[:12]


# ── Agent 1 输出：商品分析 ──

class MarketingAngles(BaseModel):
    """可宣传方向 + 合规红线"""
    selling_points: list[str] = Field(default_factory=list, description="核心卖点")
    marketing_angles: list[str] = Field(default_factory=list, description="可宣传角度")
    copy_suggestions: list[str] = Field(default_factory=list, description="文案建议")
    emotional_appeal: str = Field(default="", description="情感诉求")
    scene_suggestions: list[str] = Field(default_factory=list, description="推荐场景")
    seasonal_tags: list[str] = Field(default_factory=list, description="季节标签")
    regulatory_red_lines: list[str] = Field(
        default_factory=list,
        description="合规红线：禁止使用的表述",
    )


class ProductAnalysis(BaseModel):
    """Agent 1 分析员的输出"""
    category: str = Field(default="", description="商品品类")
    sub_category: str = Field(default="", description="子品类")
    dosage_form: str = Field(default="", description="剂型（胶囊/口服液/片剂等）")
    ingredients: list[str] = Field(default_factory=list, description="主要成分")
    features: list[str] = Field(default_factory=list, description="商品特征")
    target_audience: dict[str, str] = Field(default_factory=dict, description="目标人群画像")
    style_constraints: dict[str, str] = Field(
        default_factory=dict, description="风格约束 {色调,光影,构图}"
    )
    special_constraints: list[str] = Field(
        default_factory=list, description="特殊约束（如不可出现人物、必须露logo等）"
    )
    marketing_angles: Optional[MarketingAngles] = None
    compliance_notes: list[str] = Field(default_factory=list, description="合规注意事项")
    confidence_score: float = Field(default=0.0, ge=0, le=100, description="置信度 0-100")


# ── Agent 2 输出：提示词 ──

class PromptVariant(BaseModel):
    """单条提示词"""
    prompt: str = ""
    negative_prompt: str = ""
    style: str = ""
    composition: str = ""


class ScenePrompt(BaseModel):
    """场景图提示词"""
    prompt: str = ""
    scene_type: str = ""   # 白底 / 场景 / 社交 / 详情
    style: str = ""
    platform: str = ""     # taobao / amazon / xiaohongshu / douyin


class ModelVariants(BaseModel):
    """多模型版本的提示词"""
    dalle: str = ""
    seedream: str = ""
    flux: str = ""
    midjourney: str = ""


class ImagePrompts(BaseModel):
    """Agent 2 提示词生成员的输出"""
    main_image: PromptVariant = Field(default_factory=PromptVariant)
    scene_images: list[ScenePrompt] = Field(default_factory=list)
    social_images: list[ScenePrompt] = Field(default_factory=list)
    model_variants: ModelVariants = Field(default_factory=ModelVariants)


# ── Agent 3 输出：生成图 ──

class GeneratedImage(BaseModel):
    """Agent 3 生图员的输出"""
    prompt_name: str = ""
    prompt_text: str = ""
    image_url: str = ""
    base64_data: str = ""
    model_used: str = ""
    generation_params: dict = Field(default_factory=dict)
    processing_status: str = "raw"


# ── Agent 4 输出：审查报告 ──

class ReviewReport(BaseModel):
    """Agent 5 审查员的输出"""
    overall_score: float = Field(default=0.0, ge=0, le=100)
    dimension_scores: dict[str, float] = Field(default_factory=dict)
    top_issues: list[str] = Field(default_factory=list)
    top_praises: list[str] = Field(default_factory=list)
    verdict: Literal["pass", "retry", "fail"] = "fail"
    needs_human_review: bool = False
    iteration: int = 0


# ── Agent 6 输出：合规报告 ──

class ComplianceReport(BaseModel):
    """Agent 6 合规审查员的输出"""
    passed: bool = False
    risk_level: Literal["low", "medium", "high"] = "low"
    violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


# ── 中心决策者 ──

class CoordinatorDecision(BaseModel):
    """中心决策者的单轮决策"""
    action: Literal["invite", "done"] = "invite"
    agent_name: str = ""
    task_brief: str = ""
    reasoning: str = ""


# ── Agent 注册 ──

class AgentMeta(BaseModel):
    """Agent 注册信息（从 config/agents/*.yaml 加载）"""
    name: str
    description: str
    version: str = "1.0.0"
    requires: list[str] = Field(default_factory=list, description="能力需求: vision/text/image/local")
    timeout_ms: int = 30_000
    retry: dict = Field(default_factory=dict)
    prompt: str = ""                        # 引用的 System Prompt 文件路径
    params: list[dict] = Field(default_factory=list)  # 可配置参数


# ── 消息 ──

class Message(BaseModel):
    """群聊中的一条消息"""
    id: str = Field(default_factory=_uid)
    turn: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    role: Literal["coordinator", "agent", "system"] = "system"
    sender: str = ""
    action: Literal["invite", "respond", "done", "error"] = "respond"
    content: dict = Field(default_factory=dict)
