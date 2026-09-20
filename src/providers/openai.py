"""OpenAI Provider — GPT-4o Vision + Text + DALL-E 3"""

import os
from src.core.config import resolve_base_url
from src.harness.pricing import estimate
from src.providers.base import BaseLLMProvider, BaseImageProvider, provider_error
from src.providers.compat import openai_compatible_chat


class OpenAILLMProvider(BaseLLMProvider):
    """GPT-4o Vision + Text（也可承载任意 OpenAI 兼容服务商：火山方舟 / 智谱 / OpenRouter …）"""

    name = "openai"
    capabilities = ["vision", "text"]

    def __init__(self, api_key: str = "", base_url: str = "", name: str = "",
                 capabilities: list[str] | None = None, label: str = "",
                 max_tokens: int = 4096):
        # 参数注入优先（自定义服务商 / 内置 ark 走这条路），否则回落官方环境变量与端点
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url or resolve_base_url("openai", "https://api.openai.com/v1")
        self.label = label or "OpenAI"
        if name:
            self.name = name
        if capabilities:
            self.capabilities = list(capabilities)
        self.max_tokens = max_tokens

    async def chat(self, messages: list[dict], model: str = "gpt-4o", json_mode: bool = False) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": self.max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
            # OpenAI 要求消息中必须包含 "json" 关键词
            has_json_hint = any(
                "json" in str(m.get("content", "")).lower()
                for m in messages
            )
            if not has_json_hint:
                body["messages"] = [
                    {"role": "system", "content": "You must respond with a valid JSON object."},
                    *messages,
                ]

        return await openai_compatible_chat(
            base_url=self.base_url,
            api_key=self.api_key,
            body=body,
            label=self.label,
            model=model,
            json_mode=json_mode,
            cost_fn=self._estimate_cost,
        )

    async def chat_with_vision(self, messages: list[dict], model: str = "gpt-4o") -> dict:
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        return await openai_compatible_chat(
            base_url=self.base_url,
            api_key=self.api_key,
            body=body,
            label=self.label,
            model=model,
            parse_json=True,
            cost_fn=self._estimate_cost,
        )

    def _estimate_cost(self, model: str, prompt_tokens: int = 0,
                       completion_tokens: int = 0) -> float | None:
        """按 input/output 分别计费；**查不到价格返回 `None`**（不猜、不回落默认价）

        此前这里写死一份价目表并回落 `(2.50, 10.00)` —— 把 gpt-4o 的价按到任何
        未知模型（含方舟豆包、自定义服务商）头上。现在统一走 `src/harness/pricing.py`：
        用户可在设置页改价（`config/pricing.yaml` 优先于内置参考价），未标定就是未知。
        """
        result = estimate(self.route, model, "text",
                          {"tokens_in": int(prompt_tokens or 0),
                           "tokens_out": int(completion_tokens or 0)})
        return result["amount"]


class OpenAIImageProvider(BaseImageProvider):
    """OpenAI 兼容图像生成（DALL-E 3 / 火山方舟 Seedream / 其他兼容端点）"""

    name = "openai"
    capabilities = ["image"]

    def __init__(self, api_key: str = "", base_url: str = "", name: str = "",
                 label: str = "", extra_body: dict | None = None,
                 drop_params: tuple[str, ...] = (), default_size: str = "1024x1024",
                 supports_reference: bool = False,
                 supported_options: tuple[str, ...] = (),
                 supports_negative_prompt: bool = False):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        # 与 LLM 同端点（coding plan / 代理同样适用于生图接口）
        self.base_url = base_url or resolve_base_url("openai", "https://api.openai.com/v1")
        self.label = label or "DALL-E"
        if name:
            self.name = name
        # 路由默认尺寸（方舟 Seedream 5.0 有最小像素要求，见 RouteSpec.image_size）
        self.default_size = default_size or "1024x1024"
        # 非官方端点（火山方舟等）：DALL-E 专有参数要剔除，另按需补充字段
        self.extra_body = dict(extra_body or {})
        self.drop_params = tuple(drop_params)
        # 文+图双条件的能力声明（由路由表注入；不支持时必须显式回报 ignored_params）
        self.supports_reference = supports_reference
        self.supported_options = tuple(supported_options)
        self.supports_negative_prompt = supports_negative_prompt

    def _split_options(self, options: dict | None) -> tuple[dict, list[str]]:
        """按路由白名单切分平台可选参数 → `(采纳的, 被忽略的键)`"""
        accepted: dict = {}
        ignored: list[str] = []
        for key, value in (options or {}).items():
            if key in self.supported_options and value is not None:
                accepted[key] = value
            else:
                ignored.append(str(key))
        return accepted, sorted(ignored)

    async def generate(
        self, prompt: str, negative_prompt: str = "", size: str = "1024x1024", model: str = "dall-e-3",
        *, reference_images: list[str] | None = None, options: dict | None = None,
    ) -> dict:
        import httpx
        import warnings

        ignored: list[str] = []
        if negative_prompt and not self.supports_negative_prompt:
            # 方舟 Seedream 不支持 negative_prompt 参数：不静默丢弃，回报给调用方
            # （调用方会把负面约束折进正向提示词）
            ignored.append("negative_prompt")
            if "quality" not in self.drop_params:
                warnings.warn(
                    f"DALL-E 不支持 negative_prompt 参数，传入值被忽略: '{negative_prompt[:100]}'"
                )
        accepted_options, ignored_options = self._split_options(options)
        ignored.extend(ignored_options)

        references = [str(ref) for ref in (reference_images or []) if str(ref or "").strip()]
        if references and not self.supports_reference:
            # 参考图是"商品身份"的唯一事实来源：丢了模型必然编造包装文字（实测事故），
            # 所以这里不能静默忽略，必须回报
            ignored.append("reference_images")
            references = []
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": "standard",
        }
        for key in self.drop_params:
            body.pop(key, None)
        body.update(self.extra_body)
        if references:
            # 文+图双条件：prompt（场景）与 image（商品身份）同时入体
            body["image"] = references
        body.update(accepted_options)

        request_params = {
            "model": model,
            "size": size,
            "image_count": len(references),
            "prompt_chars": len(prompt or ""),
            "reference_bytes": sum(len(ref) for ref in references),
            **accepted_options,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.base_url}/images/generations",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return provider_error(self.label, resp, self.base_url, self.api_key)

            data = resp.json()
            items = data.get("data") or []
            if not items:
                # 200 但没有图像（上游异常/被内容策略拦下且不回错误码）：当失败上报，
                # 否则会写入"空图"记录，让协调者以为生图已完成（实测事故）
                return {"error": f"{self.label} 未返回图像（响应缺少 data 数组）"}
            image_url = items[0].get("url", "")
            revised_prompt = items[0].get("revised_prompt", prompt)

            return {
                "image_url": image_url,
                "base64_data": items[0].get("b64_json", "") or "",  # 部分端点只回 b64_json
                "revised_prompt": revised_prompt,
                "model_used": model,
                "cost_usd": self._estimate_cost(model, size),
                # 产物/审计用：这次到底用的什么条件（绝不回传 base64 本体，否则 checkpoint 爆）
                "reference_count": len(references),
                "ignored_params": sorted(set(ignored)),
                "request_params": request_params,
            }

    def _estimate_cost(self, model: str, size: str = "") -> float | None:
        """按**模型 id + 尺寸**查价；查不到返回 `None`

        此前是 `{"1024x1024": 0.04, "1024x1792": 0.08, "1792x1024": 0.08}.get(size, 0.04)`：
        尺寸与模型都不参与，未命中一律 0.04。我们实际用的方舟
        `doubao-seedream-5-0-260128` 出图尺寸是 2048x2048 —— 从来没命中过任何键，
        每次都按 DALL·E 3 的 0.04 记账（用户质疑"不及时更新价格就是很大的误导"）。
        现在：按模型查（`dall-e-3` 会再按 size 分档），未标定 → `None`，界面显示"未标定"。
        """
        model = model or "dall-e-3"
        result = estimate(self.route, model, "image",
                          {"images": 1, "size": size or self.default_size})
        return result["amount"]
