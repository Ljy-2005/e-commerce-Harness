"""Provider 抽象接口"""

from abc import ABC, abstractmethod


def provider_error(label: str, resp, endpoint: str = "", secret: str = "",
                   limit: int = 300) -> dict:
    """统一的 Provider 错误结构：状态码 + 端点 + 上游响应原文（截断）。

    只回状态码会让用户无法区分「Key 错 / 模型不存在 / 端点路径错 / 额度不足」——
    第三方 coding plan 场景尤其致命（配错端点或模型只能看到 "400"）。
    上游原文可能回显请求信息：命中密钥时做遮蔽，绝不把 Key 带进日志/界面。
    """
    detail = ""
    try:
        detail = " ".join(str(getattr(resp, "text", "") or "").split())[:limit]
    except Exception:
        detail = ""
    if secret and detail:
        detail = detail.replace(secret, "***")
    parts = [f"{label} API error: {resp.status_code}"]
    if endpoint:
        parts.append(f"@ {endpoint}")
    if detail:
        parts.append(f"— {detail}")
    return {"error": " ".join(parts)}


class BaseLLMProvider(ABC):
    """LLM Provider 抽象接口"""

    name: str = "base"
    capabilities: list[str] = []  # ["vision", "text"]
    # 该实例所属的**路由 id**（由 ProviderRegistry 注入；自定义服务商是用户起的 id）。
    # 价格查询需要它：`config/pricing.yaml` 的键可以是 `路由/模型`，同一模型在不同
    # 服务商（官方 vs 中转）价格并不相同 —— 只按模型名查会张冠李戴。
    route: str = ""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        model: str = "",
        json_mode: bool = False,
    ) -> dict:
        """发送文本对话请求，返回 {"content": dict, "tokens_used": int, "cost_usd": float|None}

        `cost_usd` 为 `None` 表示**该模型价格未标定**（见 `src/harness/pricing.py`）：
        用量如实回报，金额不猜。调用方必须显式处理 None（不要 `or 0.0`）。
        """
        ...

    async def chat_with_vision(
        self,
        messages: list[dict],
        model: str = "",
    ) -> dict:
        """发送视觉对话请求。不支持 Vision 的 Provider 返回顶层错误。"""
        return {
            "error": f"{self.name} 不支持视觉能力",
            "content": {},
            "tokens_used": 0,
            "cost_usd": 0.0,
        }


class BaseImageProvider(ABC):
    """Image Provider 抽象接口

    ## 文+图双条件（用户要求："要保证在生图的过程中要文＋图生图，而不是单纯的图生图或者文生图"）

    `generate()` 同时接受**文本提示词**与**参考图**：
    - 文本提示词负责"场景"（背景/构图/视角/光影/画幅/主体占比/风格/用途）；
    - 参考图负责"商品身份"（品牌文字、包装文案、图案、配色、形制、规格）。

    路由能力用两个类属性声明，**不支持就必须显式回报 `ignored_params`**：
    - `supports_reference`：该路由能否吃参考图；
    - `supported_options`：该路由额外接受哪些平台参数（如方舟的 `watermark`/`output_format`）。
    """

    name: str = "base"
    capabilities: list[str] = []  # ["image"]
    # 该实例所属的路由 id（由 ProviderRegistry 注入）；价格表按 `路由/模型` 精确查询
    route: str = ""
    # 该路由的默认出图尺寸（由路由表 RouteSpec.image_size 注入；见 routes.py 的说明）
    default_size: str = "1024x1024"
    # 能否接受参考图（参考图 = 商品身份的事实来源；不支持的模型只能靠文字，必然编造包装文字）
    supports_reference: bool = False
    # 该路由额外支持的可选参数（白名单之外的一律进 ignored_params）
    supported_options: tuple[str, ...] = ()
    # 是否原生支持负面提示词（方舟 Seedream 不支持 → 调用方须把负面约束折进正向文本）
    supports_negative_prompt: bool = False

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        size: str = "1024x1024",
        model: str = "",
        *,
        reference_images: list[str] | None = None,
        options: dict | None = None,
    ) -> dict:
        """生成图片

        Args:
            prompt: 文本条件（场景/构图/光影/背景/画幅）
            reference_images: 参考图（`data:` URI 列表；商品身份的事实来源）
            options: 平台可选参数（如 `{"watermark": False, "output_format": "jpeg"}`）

        Returns:
            `{"image_url", "base64_data", "cost_usd", "model_used", ...}`
            另含 `reference_count` / `ignored_params` / `request_params` 供审计与产物记录。
            `cost_usd` 为 `None` 表示价格未标定（**用量照报，金额不猜**）。
        """
        ...

    @staticmethod
    def _parse_size(size: str) -> tuple[int, int]:
        """解析尺寸字符串 "WxH" → (width, height)，所有 Image Provider 共用"""
        if "x" in size:
            w_str, h_str = size.lower().replace("x", " ").split()
            return int(w_str), int(h_str)
        return 1024, 1024
