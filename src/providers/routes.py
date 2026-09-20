"""服务商目录 — 内置路由表 + 用户自定义服务商 + 一键预设（统一事实来源）

背景（用户反馈）：
1. "我无法自己添加模型服务商，局限性太大了" —— 此前路由、凭据槽、模型目录、可用密钥
   全部硬编码在 `src/api/main.py`（`_PROVIDER_ROUTES` / `_API_KEY_META` /
   `_MODEL_CATALOG` / `_ALLOWED_SECRET_KEYS`）与 `ProviderRegistry.__init__`
   的逐个 `if os.getenv(...)` 里，用户一个都改不了。
2. "即梦不是一个模型提供商，它是存在于火山引擎里可调用的模型" —— 正确：即梦是字节的
   消费者产品，Seedream/Seedance 是**模型**，服务商是**火山引擎方舟（Ark）**
   （OpenAI 兼容端点 `https://ark.cn-beijing.volces.com/api/v3`，模型 id 形如
   `doubao-seedream-4-0-250828`）。原 `seedream` 路由打的是另一条旧通道
   （`visual.volcengineapi.com` + `req_key=jimeng_t2i_v51` 的 AK/SK 签名接口），
   现降级为"火山视觉智能（旧版）"保留兼容。

`kind` 决定用哪套协议实现：
- `openai`    → OpenAI 兼容（chat/completions + images/generations）
- `anthropic` → Anthropic Messages 兼容
- `volc_cv`   → 火山视觉智能旧版 CV 签名接口（AK/SK）
- `flux`      → FLUX 多上游（BFL / Fal.ai / Replicate）
"""

from dataclasses import dataclass, field

CAPABILITIES = ("text", "vision", "image")
KINDS = ("openai", "anthropic", "volc_cv", "flux")

# 路由 id / 环境变量名 的格式约束（也是 API 校验依据）
ROUTE_ID_RE = r"^[a-z][a-z0-9_-]{1,31}$"
ENV_NAME_RE = r"^[A-Z][A-Z0-9_]{2,63}$"


@dataclass
class RouteSpec:
    """一个服务商路由的完整定义（内置与自定义共用同一结构）"""

    route: str
    label: str
    kind: str
    capabilities: list[str] = field(default_factory=list)
    default_base_url: str = ""
    base_url_supported: bool = True
    base_url_envs: tuple[str, ...] = ()
    key_envs: tuple[str, ...] = ()          # 任一存在即可用（内置多凭据场景）
    all_key_envs: tuple[str, ...] = ()      # 必须**全部**存在才可用（AK/SK 成对签名）
    credentials: list[dict] = field(default_factory=list)   # [{name, env}] 凭据槽
    credential_hint: str = ""
    models: list[str] = field(default_factory=list)          # 官方/预设模型目录（扁平，供设置页展示）
    # 按能力分组的模型（用于"测试连接该发哪个 model"与前端按能力给建议）：
    # 一个服务商常常同时提供文本/视觉/生图模型，扁平列表会把生图模型建议到文本能力上
    models_by_capability: dict[str, list[str]] = field(default_factory=dict)
    custom: bool = False
    api_key_env: str = ""                   # 自定义路由：单一凭据变量名
    deprecated: bool = False                # 已弃用通道（前端给引导横幅）
    deprecated_hint: str = ""
    # 生图默认尺寸：部分平台对像素数有硬下限（方舟 Seedream 5.0 要求 ≥ 3,686,400
    # 像素，即至少约 1920×1920，传 1024x1024 直接 400 InvalidParameter）。
    # 用户可在 config/models.yaml 的 capabilities.image.size 覆盖。
    image_size: str = "1024x1024"
    # 该路由的生图能否吃**参考图**（文+图双条件里的"图"）。
    # 方舟 Seedream 支持单图生单图/多参考图（`image` 传 data URI 数组）；DALL-E 3、
    # 旧版 Seedream AK/SK 通道、FLUX 都不支持 —— 不支持时必须显式回报，不能静默忽略
    # （参考图是商品身份的唯一事实来源，丢了模型必然编造包装文字）。
    supports_reference: bool = False
    # 该路由额外接受的平台参数（白名单）：方舟支持 `watermark`（关掉"AI生成"水印）与
    # `output_format`；白名单之外的键一律进 `ignored_params`。
    image_options: tuple[str, ...] = ()
    # 是否原生支持 negative_prompt（方舟 Seedream 不支持 → 调用方须把负面约束折进正向文本）
    supports_negative_prompt: bool = False
    # LLM 单次输出上限：推理模型（DeepSeek V4 等）的思考 token 也计入该预算，
    # 4096 会被"想完就没额度了"吃光 → content 空串（实测 reasoning_tokens=4097）。
    max_tokens: int = 4096

    def models_for(self, capability: str) -> list[str]:
        """该能力可用的模型列表：优先按能力分组，缺省回落扁平列表"""
        grouped = self.models_by_capability.get(capability)
        if grouped:
            return list(grouped)
        return list(self.models)

    def capability_text(self) -> str:
        return " / ".join(self.capabilities)

    def is_image_only(self) -> bool:
        return "image" in self.capabilities and "vision" not in self.capabilities \
            and "text" not in self.capabilities


# ── 内置路由表 ──

BUILTIN_SPECS: list[RouteSpec] = [
    RouteSpec(
        route="openai", label="OpenAI", kind="openai",
        capabilities=["vision", "text", "image"],
        default_base_url="https://api.openai.com/v1",
        base_url_envs=("OPENAI_BASE_URL",),
        key_envs=("OPENAI_API_KEY",),
        credentials=[{"name": "OpenAI", "env": "OPENAI_API_KEY"}],
        credential_hint="官方 Key 或第三方 coding plan / 代理 Key",
        models=["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini", "dall-e-3"],
        models_by_capability={
            "text": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3", "o4-mini"],
            "vision": ["gpt-4o", "gpt-4.1", "gpt-4o-mini"],
            "image": ["dall-e-3"],
        },
    ),
    RouteSpec(
        route="deepseek", label="DeepSeek", kind="openai",
        capabilities=["text", "vision"],
        default_base_url="https://api.deepseek.com/v1",
        base_url_envs=("DEEPSEEK_BASE_URL",),
        key_envs=("DEEPSEEK_API_KEY",),
        credentials=[{"name": "DeepSeek", "env": "DEEPSEEK_API_KEY"}],
        credential_hint="官方 Key 或兼容端点 Key",
        models=["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"],
        # V4 是推理模型：实测 4096 会被思考 token 吃光（reasoning_tokens=4097、
        # content=""、finish_reason=length）；同一 prompt 用 16384 得到
        # finish_reason=stop + 3470 字正文（思考 10276 tokens，耗时 75s）。
        max_tokens=16384,
    ),
    RouteSpec(
        route="anthropic", label="Anthropic", kind="anthropic",
        capabilities=["vision", "text"],
        default_base_url="https://api.anthropic.com/v1",
        base_url_envs=("ANTHROPIC_BASE_URL",),
        key_envs=("ANTHROPIC_API_KEY",),
        credentials=[{"name": "Anthropic", "env": "ANTHROPIC_API_KEY"}],
        credential_hint="官方 Key 或 Claude 兼容中转（coding plan 常见）",
        models=["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
    ),
    RouteSpec(
        route="qwen", label="通义千问（Qwen）", kind="openai",
        capabilities=["vision", "text"],
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        base_url_envs=("DASHSCOPE_BASE_URL", "QWEN_BASE_URL"),
        key_envs=("DASHSCOPE_API_KEY",),
        credentials=[{"name": "通义千问（Qwen）", "env": "DASHSCOPE_API_KEY"}],
        credential_hint="DashScope Key 或兼容端点 Key",
        models=["qwen-max", "qwen-vl-max", "qwen-plus"],
    ),
    # 火山引擎方舟：即梦/Seedream、豆包等模型真正的服务商（OpenAI 兼容）
    RouteSpec(
        route="ark", label="火山引擎方舟（Ark）", kind="openai",
        capabilities=["text", "vision", "image"],
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        base_url_envs=("ARK_BASE_URL",),
        key_envs=("ARK_API_KEY",),
        credentials=[{"name": "火山方舟 Ark", "env": "ARK_API_KEY"}],
        credential_hint="方舟 API Key（即梦/Seedream 与豆包模型都在这里调用）",
        # 实测：`size=1024x1024` → 400「image size must be at least 3686400 pixels」；
        # `size=2048x2048` → 200（约 30s 出图）。2048×2048 同时满足淘系主图 1:1。
        image_size="2048x2048",
        # 文+图双条件：Seedream 支持参考图（单图生单图 / 2-14 张多参考图）；
        # `watermark: false` 用来关掉平台默认的 "AI生成" 水印（实测出图右下角有）；
        # `output_format` 让返回的格式可控（默认 jpeg）。
        supports_reference=True,
        image_options=("watermark", "output_format"),
        # 方舟 Seedream **不支持** negative_prompt 参数 → 负面约束由调用方折进正向提示词
        supports_negative_prompt=False,
        # 以下 id 用真实账号 `GET /api/v3/models` 校准过（2026-09）：
        # 方舟模型 id 是**小写 + 短横线 + 日期后缀**（如 doubao-seedream-5-0-260128），
        # 写成 Doubao-Seedream-5.0-lite 这类形态会 404 InvalidEndpointOrModel.NotFound。
        # 设置页「拉取可用模型」可随时按账号实际可见列表刷新。
        models=[
            "doubao-seedream-5-0-260128", "doubao-seedream-5-0-pro-260628",
            "doubao-seedream-4-5-251128", "doubao-seedream-4-0-250828",
            "doubao-seed-2-1-pro-260628", "doubao-seed-2-1-turbo-260628",
            "doubao-seed-2-0-pro-260215", "doubao-seed-2-0-lite-260428",
        ],
        # 方舟同时提供文本/视觉/生图模型：必须按能力区分，否则会把生图模型发给 chat 接口
        models_by_capability={
            "image": ["doubao-seedream-5-0-260128", "doubao-seedream-5-0-pro-260628",
                      "doubao-seedream-4-5-251128", "doubao-seedream-4-0-250828"],
            "vision": ["doubao-seed-2-1-pro-260628", "doubao-seed-2-0-pro-260215",
                       "doubao-seed-2-0-lite-260428"],
            "text": ["doubao-seed-2-1-pro-260628", "doubao-seed-2-1-turbo-260628",
                     "doubao-seed-2-0-pro-260215", "doubao-seed-2-0-lite-260428"],
        },
    ),
    # 旧通道：火山视觉智能 CV 签名接口（AK/SK）与即梦平台 Key，保留兼容
    RouteSpec(
        route="seedream", label="火山视觉智能（旧版 AK/SK 签名）", kind="volc_cv",
        capabilities=["image"],
        default_base_url="", base_url_supported=False,
        key_envs=("SEEDREAM_API_KEY",),
        all_key_envs=("VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY"),   # AK/SK 必须成对
        credentials=[
            {"name": "即梦平台 Key（旧）", "env": "SEEDREAM_API_KEY"},
            {"name": "火山引擎 AccessKey", "env": "VOLCANO_ACCESS_KEY"},
            {"name": "火山引擎 SecretKey", "env": "VOLCANO_SECRET_KEY"},
        ],
        credential_hint="旧版视觉智能接口（req_key=jimeng_t2i_v51）；新项目请用「火山引擎方舟（Ark）」",
        models=["seedream-5.0", "seedream-4.0"],
        deprecated=True,
        deprecated_hint=(
            "这是旧版签名通道（AK/SK 成对，或即梦平台 Key）。"
            "**如果你用的是火山方舟 API Key（ark- 开头），它属于「火山引擎方舟（Ark）」卡片**——"
            "即梦/Seedream 与豆包模型都在方舟调用，单把 API Key 即可，无需 AK/SK。"
        ),
    ),
    RouteSpec(
        route="flux", label="FLUX", kind="flux",
        capabilities=["image"],
        default_base_url="", base_url_supported=False,
        key_envs=("BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY"),
        credentials=[
            {"name": "BFL (api.bfl.ml)", "env": "BFL_API_KEY"},
            {"name": "Fal.ai", "env": "FAL_KEY"},
            {"name": "Replicate", "env": "REPLICATE_API_KEY"},
        ],
        credential_hint="BFL / Fal.ai / Replicate 任选其一",
        models=["flux.1-dev", "flux.1-schnell", "flux-pro"],
    ),
]

BUILTIN_BY_ROUTE = {s.route: s for s in BUILTIN_SPECS}


# ── 一键预设（常见 OpenAI / Anthropic 兼容服务商）──
#
# 说明：模型 id 与端点会随官方调整，预设只是"填好起始值"，用户可在卡片里改。
# `key_env` 是建议的凭据变量名（也允许用户改）。

PROVIDER_PRESETS: list[dict] = [
    {
        "route": "zhipu", "label": "智谱 GLM", "kind": "openai",
        "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_key_env": "ZHIPU_API_KEY",
        "capabilities": ["text", "vision"], "models": ["glm-4.6", "glm-4v-plus"],
        "credential_hint": "智谱开放平台 Key（GLM 系列）",
    },
    {
        "route": "moonshot", "label": "Moonshot Kimi", "kind": "openai",
        "base_url": "https://api.moonshot.cn/v1", "api_key_env": "MOONSHOT_API_KEY",
        "capabilities": ["text", "vision"],
        "models": ["kimi-k2-0905-preview", "moonshot-v1-8k-vision-preview"],
        "credential_hint": "Moonshot 开放平台 Key",
    },
    {
        "route": "siliconflow", "label": "硅基流动 SiliconFlow", "kind": "openai",
        "base_url": "https://api.siliconflow.cn/v1", "api_key_env": "SILICONFLOW_API_KEY",
        "capabilities": ["text", "vision", "image"],
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-VL-72B-Instruct",
                   "Kwai-Kolors/Kolors"],
        "credential_hint": "硅基流动 API Key（聚合多家开源模型）",
    },
    {
        "route": "openrouter", "label": "OpenRouter", "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY",
        "capabilities": ["text", "vision"],
        "models": ["openai/gpt-4o", "anthropic/claude-sonnet-4", "google/gemini-2.5-pro"],
        "credential_hint": "OpenRouter Key（一个 Key 调多家模型）",
    },
    {
        "route": "ark", "label": "火山引擎方舟（Ark）", "kind": "openai",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3", "api_key_env": "ARK_API_KEY",
        "capabilities": ["text", "vision", "image"],
        "models": ["doubao-seedream-4-5-251128", "doubao-seedream-4-0-250828",
                   "doubao-seed-1-6-vision-250815"],
        "credential_hint": "方舟 API Key（即梦/Seedream 与豆包模型）",
    },
    {
        "route": "ollama", "label": "Ollama（本地）", "kind": "openai",
        "base_url": "http://localhost:11434/v1", "api_key_env": "OLLAMA_API_KEY",
        "capabilities": ["text", "vision"], "models": ["qwen2.5vl", "llama3.1"],
        "credential_hint": "本地服务无需真 Key，随便填一个非空值即可",
    },
]


def preset_by_route(route: str) -> dict | None:
    return next((dict(p) for p in PROVIDER_PRESETS if p["route"] == route), None)


# ── 校验（API 与注册表共用，保证写进文件的一定是合法结构）──

def validate_spec(raw: dict, existing_routes: set[str] | None = None,
                  *, allow_builtin_override: bool = False) -> tuple[dict, list[str]]:
    """校验并规整一条自定义服务商定义，返回 (spec, errors)

    - `route`：小写字母开头，仅 `a-z0-9_-`，长度 2-32，不得与内置路由冲突；
    - `kind`：openai / anthropic（volc_cv 与 flux 属内置特殊协议，不允许自定义）；
    - `capabilities`：text/vision/image 的非空子集；
    - `api_key_env`：大写环境变量名；
    - `base_url`：http(s) 且能解析出主机（Ollama 之类本地端点也满足）。
    """
    import re
    from urllib.parse import urlparse

    errors: list[str] = []
    existing_routes = existing_routes or set()

    route = str(raw.get("route") or "").strip().lower()
    if not re.match(ROUTE_ID_RE, route):
        errors.append("route 需为 2-32 位小写字母开头的 id（a-z0-9_-）")
    elif route in existing_routes:
        errors.append(f"路由 '{route}' 已存在（请换一个 id 或先删除原服务商）")
    elif route in BUILTIN_BY_ROUTE and not allow_builtin_override:
        errors.append(f"路由 '{route}' 已存在（内置服务商，请换一个 id）")

    label = str(raw.get("label") or "").strip()[:60]
    if not label:
        errors.append("label（展示名）不能为空")

    kind = str(raw.get("kind") or "openai").strip().lower()
    if kind not in ("openai", "anthropic"):
        errors.append("kind 只支持 openai（OpenAI 兼容）或 anthropic（Anthropic 兼容）")

    caps = [str(c).strip().lower() for c in (raw.get("capabilities") or [])]
    caps = [c for c in dict.fromkeys(caps) if c]
    bad_caps = [c for c in caps if c not in CAPABILITIES]
    if bad_caps:
        errors.append(f"capabilities 含未知能力: {', '.join(bad_caps)}")
    if not caps:
        errors.append("capabilities 至少选一项（text/vision/image）")

    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        errors.append("base_url 必须是可解析的 http(s) 地址（例：https://open.bigmodel.cn/api/paas/v4）")

    api_key_env = str(raw.get("api_key_env") or "").strip().upper()
    if not re.match(ENV_NAME_RE, api_key_env):
        errors.append("api_key_env 需为大写环境变量名（例：ZHIPU_API_KEY）")

    models: list[str] = []
    for item in (raw.get("models") or []):
        mid = str(item).strip()
        if not mid:
            continue
        if len(mid) > 200 or not all(0x21 <= ord(ch) <= 0x7E for ch in mid):
            errors.append(f"模型 id 非法（仅可打印 ASCII，无空格）: {mid[:40]}")
            continue
        if mid not in models:
            models.append(mid)
    if len(models) > 50:
        errors.append("模型最多 50 个")

    spec = {
        "route": route, "label": label, "kind": kind, "capabilities": caps,
        "base_url": base_url, "api_key_env": api_key_env, "models": models,
        "credential_hint": str(raw.get("credential_hint") or "").strip()[:200],
    }
    return spec, errors


def spec_from_custom(raw: dict, occupied: set[str] | None = None) -> tuple["RouteSpec | None", list[str]]:
    """`config/custom_providers.yaml` 的一条记录 → RouteSpec

    返回 (spec, errors)；结构非法时 spec 为 None 并把原因带回（加载侧跳过该条并告警，
    不让一条坏记录毁掉整个服务商列表）。
    """
    if not isinstance(raw, dict):
        return None, ["服务商记录必须是对象"]
    # allow_builtin_override：文件里若与内置路由重名，由 occupied（内置+已加载自定义）判定，
    # 此处只做结构校验，避免把"重名"误报成"结构非法"
    spec, errors = validate_spec(raw, existing_routes=set(), allow_builtin_override=True)
    route = spec.get("route", "")
    if route and occupied and route in occupied:
        errors.append(f"路由 '{route}' 与已有服务商重名")
    if errors or not route:
        return None, errors or ["route 缺失"]
    return RouteSpec(
        route=route,
        label=spec["label"] or route,
        kind=spec["kind"],
        capabilities=list(spec["capabilities"]),
        default_base_url=spec["base_url"],
        base_url_supported=True,
        base_url_envs=(f"{route.upper()}_BASE_URL",),
        key_envs=(spec["api_key_env"],),
        credentials=[{"name": spec["label"] or route, "env": spec["api_key_env"]}],
        credential_hint=spec["credential_hint"],
        models=list(spec["models"]),
        custom=True,
        api_key_env=spec["api_key_env"],
    ), []
