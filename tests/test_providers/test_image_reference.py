"""文+图双条件生图 —— Provider 层必须同时把「文本提示词」和「参考图」送上去

用户明确要求："你要保证在生图的过程中要文＋图生图，而不是单纯的图生图或者文生图"。

实测现状：`OpenAIImageProvider.generate()` 的请求体只有 `model/prompt/n/size/quality`，
`ImageGeneratorAgent` 也从不读 `task.product_images` → **参考图从未进入生图环节**，
所以模型只能凭文字编造包装上的品牌（`DEFOEBUENA®` → `NUTRIVA®`）。

这里钉住四件事：
1. 有参考图时，请求体里 `prompt` 与 `image` **同时存在**（缺一不可）；
2. 平台参数（`watermark` / `output_format`）按路由白名单写入，**被丢弃的参数必须回报
   `ignored_params`**（此前的静默丢弃与 A41"死配置"同族）；
3. 不支持参考图的路由（旧 Seedream AK/SK、FLUX）被要求时必须显式回报，不静默忽略；
4. 请求参数（张数/尺寸/水印/输出格式）回传给产物与审计，便于复盘"这张图到底怎么生成的"。
"""

import httpx as _httpx_module
import pytest

from src.providers.openai import OpenAIImageProvider
from src.providers.routes import BUILTIN_BY_ROUTE


class _FakeResponse:
    def __init__(self, status_code: int = 200, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data

    @property
    def text(self) -> str:
        import json as _json
        return _json.dumps(self._data, ensure_ascii=False)


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, headers=None, json=None):
        self.requests.append({"url": url, "headers": headers or {}, "json": json})
        return self._response


@pytest.fixture
def http(monkeypatch):
    state = {"response": _FakeResponse(200, {"data": [{"url": "https://img/1.jpeg"}]}),
             "client": None}

    def _make_client(*args, **kwargs):
        state["client"] = _FakeAsyncClient(state["response"])
        return state["client"]

    monkeypatch.setattr(_httpx_module, "AsyncClient", _make_client)
    return state


def _ark_provider() -> OpenAIImageProvider:
    """模拟注册表为方舟路由构造的实例（支持参考图 + watermark/output_format）"""
    return OpenAIImageProvider(
        api_key="ark-test", base_url="https://ark.cn-beijing.volces.com/api/v3",
        name="ark", label="火山引擎方舟（Ark）",
        extra_body={"response_format": "url"}, drop_params=("quality",),
        default_size="2048x2048",
        supports_reference=True, supported_options=("watermark", "output_format"),
    )


DATA_URI = "data:image/png;base64,iVBORw0KGgo="


class TestReferenceAndPromptTogether:
    async def test_prompt_and_image_are_sent_together(self, http):
        provider = _ark_provider()
        result = await provider.generate(
            prompt="商品本体正面平视，纯白背景 #FFFFFF",
            size="2048x2048", model="doubao-seedream-5-0-260128",
            reference_images=[DATA_URI],
        )
        body = http["client"].requests[0]["json"]
        assert body["prompt"].startswith("商品本体")
        assert body["image"] == [DATA_URI], "参考图必须与文本提示词同时入体（文+图）"
        assert result["reference_count"] == 1
        assert result["ignored_params"] == []

    async def test_multiple_references(self, http):
        provider = _ark_provider()
        await provider.generate("x", size="2048x2048", model="m",
                                reference_images=[DATA_URI, DATA_URI])
        assert http["client"].requests[0]["json"]["image"] == [DATA_URI, DATA_URI]

    async def test_no_reference_means_no_image_key(self, http):
        provider = _ark_provider()
        result = await provider.generate("x", size="2048x2048", model="m")
        body = http["client"].requests[0]["json"]
        assert "image" not in body
        assert result["reference_count"] == 0
        assert result["ignored_params"] == []

    async def test_unsupported_route_reports_reference_ignored(self, http):
        """不支持参考图的路由（如 DALL-E）被要求时必须显式回报，不能静默忽略"""
        provider = OpenAIImageProvider(api_key="sk-test", name="openai", label="DALL-E")
        result = await provider.generate("x", reference_images=[DATA_URI])
        assert "image" not in http["client"].requests[0]["json"]
        assert "reference_images" in result["ignored_params"]
        assert result["reference_count"] == 0


class TestPlatformOptions:
    async def test_watermark_and_output_format_applied(self, http):
        provider = _ark_provider()
        result = await provider.generate(
            "x", size="2048x2048", model="m",
            options={"watermark": False, "output_format": "jpeg"},
        )
        body = http["client"].requests[0]["json"]
        assert body["watermark"] is False, "必须显式关掉平台水印（实测出图右下角有 AI生成 水印）"
        assert body["output_format"] == "jpeg"
        assert result["ignored_params"] == []

    async def test_unsupported_option_is_reported_not_silently_dropped(self, http):
        provider = _ark_provider()
        result = await provider.generate(
            "x", size="2048x2048", model="m",
            options={"watermark": False, "sequential_image_generation": "auto"},
        )
        assert "sequential_image_generation" in result["ignored_params"]
        assert "sequential_image_generation" not in http["client"].requests[0]["json"]

    async def test_route_without_options_reports_all_ignored(self, http):
        provider = OpenAIImageProvider(api_key="sk-test", name="openai", label="DALL-E")
        result = await provider.generate("x", options={"watermark": False})
        assert result["ignored_params"] == ["watermark"]

    async def test_request_params_returned_for_audit(self, http):
        provider = _ark_provider()
        result = await provider.generate(
            "一段提示词", size="2048x2048", model="doubao-seedream-5-0-260128",
            reference_images=[DATA_URI], options={"watermark": False},
        )
        params = result["request_params"]
        assert params["size"] == "2048x2048"
        assert params["model"] == "doubao-seedream-5-0-260128"
        assert params["image_count"] == 1
        assert params["watermark"] is False
        assert params["prompt_chars"] == len("一段提示词")
        # 绝不把 base64 塞回产物（checkpoint 会爆）
        assert "image" not in params and DATA_URI not in str(params)


class TestRouteSpecCapabilities:
    def test_ark_declares_reference_and_options(self):
        ark = BUILTIN_BY_ROUTE["ark"]
        assert ark.supports_reference is True
        assert "watermark" in ark.image_options
        assert ark.image_size, "方舟有最小像素要求，必须保留默认尺寸"

    def test_legacy_routes_do_not_claim_reference_support(self):
        for route in ("seedream", "flux"):
            spec = BUILTIN_BY_ROUTE.get(route)
            if spec is not None:
                assert spec.supports_reference is False, f"{route} 不应谎报支持参考图"
