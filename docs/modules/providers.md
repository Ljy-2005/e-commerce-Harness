# Providers — AI 服务商适配层

> 覆盖: `src/providers/routes.py`（服务商目录表）, `src/providers/__init__.py`, `src/providers/base.py`, `src/providers/compat.py`（OpenAI 兼容调用与输出守卫）, `src/providers/json_parse.py`（宽松 JSON 解析）, `src/providers/mock.py`, `src/providers/openai.py`, `src/providers/deepseek.py`, `src/providers/anthropic.py`, `src/providers/seedream.py`, `src/providers/qwen.py`, `src/providers/flux.py`, `config/models.yaml`, `config/providers.yaml`, `config/custom_providers.yaml`

## 功能

Provider 层封装了所有外部 AI 服务的调用细节，提供统一的 LLM/Image Provider 接口。核心职责：

- **能力声明** — 每个 Provider 声明自己能提供的能力（vision / text / image）
- **路由表驱动**（本轮重构）— 服务商目录（路由 / 凭据槽 / 模型目录 / 可用密钥）统一来自
  `src/providers/routes.py`：内置 7 条 + **用户自定义服务商**。此前这些是 API 与注册表里的
  静态硬编码，用户无法新增服务商
- **自动检测** — 按路由表检查凭据环境变量（`all_key_envs` 表示必须成对，如火山 AK/SK）
- **能力解析** — Agent 声明 `requires: ["vision"]` → ProviderRegistry 根据 config/models.yaml 解析出具体 Provider + Model
- **延迟实例化** — 真实 Provider 首次调用时才初始化，避免启动时检查 API Key
- **端点可覆盖** — 每个路由的 base URL 支持三级覆盖：环境变量（`<ROUTE>_BASE_URL`）→
  `config/providers.yaml` → 表内默认端点，用于第三方 coding plan / 中转 / 自建网关

## Provider 全景（内置路由表）

| 路由 | 展示名 | kind | 能力 | 凭据 | 说明 |
|------|--------|------|------|------|------|
| `openai` | OpenAI | openai | vision, text, image | `OPENAI_API_KEY` | GPT-4o + DALL-E 3 |
| `deepseek` | DeepSeek | openai | text, vision | `DEEPSEEK_API_KEY` | V4 代（flash / pro / flash-vision-exp） |
| `anthropic` | Anthropic | anthropic | vision, text | `ANTHROPIC_API_KEY` | Messages API |
| `qwen` | 通义千问 | openai | vision, text | `DASHSCOPE_API_KEY` | DashScope 兼容模式 |
| **`ark`** | **火山引擎方舟（Ark）** | openai | text, vision, **image** | `ARK_API_KEY` | **即梦/Seedream 与豆包模型真正的服务商**；端点 `https://ark.cn-beijing.volces.com/api/v3`，生图走 OpenAI 兼容 `/images/generations`（`response_format=url`），模型 id 形如 `doubao-seedream-4-5-251128` |
| `seedream` | 火山视觉智能（**旧版** AK/SK 签名） | volc_cv | image | `SEEDREAM_API_KEY` 或 `VOLCANO_ACCESS_KEY`+`VOLCANO_SECRET_KEY`（**成对**） | 旧通道：`visual.volcengineapi.com` + `req_key=jimeng_t2i_v51` |
| `flux` | FLUX | flux | image | `BFL_API_KEY` / `FAL_KEY` / `REPLICATE_API_KEY` | 三后端（BFL / Fal.ai / Replicate） |
| `mock` | Mock | — | 全部 | 无需 | 模板数据（始终可用） |

> **概念澄清**（用户订正）：**即梦**是字节的消费者产品，**Seedream/Seedance 是模型**，
> 服务商是**火山引擎方舟**。原表把"即梦"当成服务商属建模错位，现新增 `ark` 路由承载
> 方舟模型，旧 CV 签名通道降级为"火山视觉智能（旧版）"保留兼容。
> `config/models.yaml` 的生图默认值也已改为 `ark/doubao-seedream-4-5-251128`，
> 旧通道与 OpenAI/FLUX 作为备选依次回落。

## 火山方舟（Ark）使用要点

用户实测踩坑（已内建引导）：

1. **即梦/Seedream 的 Key 属于 `ark` 路由**，不是旧的「火山视觉智能」卡片。
   方舟 API Key 以 `ark-` 开头、单把即可；旧卡片要 `VOLCANO_ACCESS_KEY` +
   `VOLCANO_SECRET_KEY` **成对**（HMAC 签名），把方舟 Key 填进 AccessKey 槽是无效的。
   旧卡片现标记 `deprecated=true`（行头「旧版通道」徽章 + 卡片内引导横幅）。
2. **测试连接会先做凭据预检**并给出可执行下一步，而不是把下游裸错误抛出来：
   - 没配凭据 → 「未配置凭据：请先填写 …」；
   - AK/SK 只配一半 → 「凭据不完整：还缺 VOLCANO_SECRET_KEY」；
   - **检测到 `ark-` 前缀的 Key 落在旧卡片** → 指出它属于 `ark`，并返回
     `suggested: {route, env, from_env}`，前端给「一键迁移」按钮
     （见 `POST /api/settings/providers/move-credential`）。
3. **模型必须显式指定**：方舟端点没有"默认模型"，不传 `model` 直接 400 `MissingParameter`。
   路由表因此引入 `models_by_capability`（文本/视觉/生图各自的模型），
   测试连接的解析顺序：自定义模型 → 模型映射中属于该路由的模型 → **该能力分组的首个官方模型**。
4. **模型 id 有固定形态**：方舟是「**小写字母 + 短横线 + 日期后缀**」，例如
   `doubao-seedream-5-0-260128`。写成 `Doubao-Seedream-5.0-lite` 这类形态必然 404
   `InvalidEndpointOrModel.NotFound`（实测案例）。内置默认 id 已用真实账号的
   `GET /api/v3/models` 校准过：
   - 生图：`doubao-seedream-5-0-260128` / `-5-0-pro-260628` / `-4-5-251128` / `-4-0-250828`
   - 文本/视觉：`doubao-seed-2-1-pro-260628` / `-2-1-turbo-260628` / `-2-0-pro-260215` / `-2-0-lite-260428`
   （平台迭代很快，所以提供了下面的"拉取"功能，别把这些当长期清单。）
5. **模型未开通/写错 id 都会返回 404**，但两者可区分（测试连接会补说明）：
   - `InvalidEndpointOrModel.NotFound`：id 不存在或无权访问 —— 若 id 里带大写字母或点号，
     还会额外提示 id 形态不对；
   - `ModelNotOpen`（「Your account … has not activated the model …」）：**账号未开通该模型**，
     需到方舟控制台「开通管理」里开通，**文本与生图要分别开通**，开通后无需改配置。
   两种情况都会先点明「鉴权已通过（Key 有效）」，避免用户误以为 Key 配错。
6. **「⬇️ 拉取可用模型」**（卡片「自定义设置」内，仅 OpenAI 兼容路由）：调用服务商的
   `GET {base_url}/models`，把**该账号可见**的模型按能力分组列出来，点一下就填入模型目录。
   实测方舟返回 133 个模型（含 `name` / `version` / `status` / `modalities`）。
   **界面显示模型名**（如 `doubao-seedream-5-0`），**填入的是完整 id**（「名称-日期」形态），
   标题里带版本号。
   过滤规则（用户实测反馈"名字有些问题"后补上）：`已下线`/`即将下线` 排除；
   专用家族（embedding / translation / smart-router / character / seed3d / seedance / ocr）排除；
   `-code-` 代码专用排除；**建议按"新 → 旧"排序**（平台目录里历史型号很多）；
   多模态模型同时进"视觉"与"文本"（只归一类会让文本里只剩纯文本模型）。
   实测过滤效果：133 个里**可推荐 28 个、过滤 105 个**（即将下线 69 / 已下线 22 / 专用 13 / 代码 1）。
7. **401 与 404 分开解释**（用户实测两次踩坑）：
   - `401 AuthenticationError`「The API key format is incorrect」→ 提示这是**鉴权失败**，
     点明方舟 Key 形态（`ark-…`，约 46 位）与常见填错项（AK/SK、`ep-…` 接入点 ID、
     别家平台 Key），以及"是否复制完整 / 是否已被轮换"；
   - 404 模型类错误 → 提示**鉴权已通过**，是模型不可用（未开通 / id 形态不对）。
   两类提示互斥，有反向断言钉住，避免把 Key 问题说成模型问题或反之。
8. **测试连接的模型选择顺序**（尤其对自定义模型列表）：① 自定义 ∩ 本能力已知模型 →
   ② 自定义里"不属于其他能力"的（coding plan 自有模型名要尊重用户）→ ③ 能力映射中属于该路由的 →
   ④ 该能力分组的官方模型。方舟的路由同时有生图与文本模型，若把生图模型填进自定义列表，
   测文本时会自动改用文本模型（实测踩到：直接取第一条会把生图模型发给 chat 接口）。
9. **生图尺寸有硬下限**（A30，实测事故）：方舟 Seedream 5.0 要求图像 **≥ 3,686,400 像素**，
   传 `1024x1024`（104 万像素）直接 400
   `InvalidParameter: image size must be at least 3686400 pixels`；`2048x2048` 实测 200（约 30s 出图）。
   因此尺寸不再写死：
   - 路由级默认：`RouteSpec.image_size`（`ark=2048x2048`，其余 `1024x1024`）；
   - 用户可覆盖：`config/models.yaml → capabilities.image.size`（非法值被拒绝并回落）；
   - 生效顺序：**config/models.yaml → 路由默认 → 1024x1024**（`resolve_image_size()`）；
   - 张数同理：`capabilities.image.variants`（默认 3，clamp 1–6）；
   - **"测试连接"的生图分支也用同一尺寸**（否则测试永远失败），并有独立的 120s 超时
     （文本测试仍是 20s）。
10. **输出预算（`max_tokens`）与推理模型**（A32）：DeepSeek V4 等推理模型的**思考 token 也计入
    `max_tokens`** —— 实测 `max_tokens=4096` 时 `reasoning_tokens=4097`、`content=""`、
    `finish_reason=length`（"调用成功但输出为空"）；同一 prompt 用 16384 得到 `stop` + 3470 字正文。
    预算来源：`RouteSpec.max_tokens`（`deepseek=16384`，其余 4096）→ 可被
    `config/providers.yaml` 的同名路由字段覆盖：

    ```yaml
    deepseek:
      max_tokens: 16384
    ```

    响应守卫在 `src/providers/compat.py`（OpenAI 兼容路由共用）：空内容/截断一律变成可读错误，
    并在 `finish_reason=length` 时**自动把预算提升 4 倍重试一次**（上限 32768）；JSON 解析走
    `src/providers/json_parse.py`（原文 → 去 ``` 围栏 → 取首个括号平衡对象）。
11. **`agent_overrides` 的键必须是该 Agent 的 `requires` 之一**（A41）：给「审查员」写 `text:`
    覆盖而它 `requires=[vision]` 时，覆盖会被**静默忽略**。后端 `resolve()` 会记 warning，
    `/api/settings` 暴露 `agent_overrides_issues`，前端覆盖行按 requires 过滤并自动纠正。

## 自定义服务商（用户可自行添加）

设置页「模型服务商 → ➕ 添加服务商」支持任意 **OpenAI 兼容**或 **Anthropic 兼容**服务商
（智谱 / Kimi / 硅基流动 / OpenRouter / 方舟 / Ollama …，并提供一键预设）：

```yaml
# config/custom_providers.yaml（已 gitignore）
providers:
- route: zhipu                    # 小写字母开头，2-32 位（a-z0-9_-）
  label: 智谱 GLM
  kind: openai                    # openai | anthropic
  base_url: https://open.bigmodel.cn/api/paas/v4
  api_key_env: ZHIPU_API_KEY      # 大写环境变量名（设置页保存 Key 的白名单同步放行）
  capabilities: [text, vision]    # text / vision / image 子集
  models: [glm-4.6, glm-4v-plus]  # 进模型映射建议列表
  credential_hint: 智谱开放平台 Key
```

- 新增后立刻重建注册表：该服务商出现在服务商列表、可保存其 Key、可被「模型映射」选为某能力的默认模型；
- 生图能力走 OpenAI 兼容 `/images/generations`（自动剔除 DALL-E 专有参数 `quality`，补 `response_format=url`）；
- 校验：route 格式/重名（内置与自定义互斥）、kind 白名单、capabilities 非空子集、
  base_url 必须 http(s)、api_key_env 必须大写变量名、模型 id 仅可打印 ASCII（≤50 个）；
- 删除：设置页卡片底部两步确认；**内置路由不可删**。

## 关键类

### BaseLLMProvider (`base.py`)
LLM 抽象接口：

```
async chat(messages, model, json_mode) → dict
async chat_with_vision(messages, model) → dict
```

### BaseImageProvider (`base.py`)
Image 抽象接口：

```
async generate(prompt, negative_prompt, size, model) → dict
```

### ProviderRegistry (`__init__.py`)
核心注册中心。启动流程：

```
1. 构造时注册 Mock（始终可用）
2. 扫描环境变量，标记可用 Provider
3. Agent 调用 resolve(requires, agent_name) 获取 (provider, model)
```

**解析顺序**（`resolve` 方法）：
```
Agent requires: ["vision"]
  → agent_overrides 有覆盖？ → 使用
  → capabilities.{cap}.default → Provider 可用？ → 使用
  → alternatives[] → 逐个检查 → 使用
  → fallback[] → 使用
  → mock（兜底）
```

### Mock Provider (`mock.py`)
MockLLMProvider + MockImageProvider，返回保健品模板数据。关键词匹配：
- "审查/质检/评分" → ReviewReport
- "合规/广告法/规范" → ComplianceReport
- "提示词/prompt" → ImagePrompts
- "分析/品类/卖点" → ProductAnalysis

### OpenAI Provider (`openai.py`)
OpenAILLMProvider + OpenAIImageProvider。GPT-4o (vision+text) + DALL-E 3 (image)。

### DeepSeek Provider (`deepseek.py`)
DeepSeekLLMProvider。V4 代（2026-08 起）：`deepseek-v4-flash`（文本默认）/ `deepseek-v4-pro`（更强）/ `deepseek-v4-flash-vision-exp`（多模态视觉，含 `chat_with_vision`）。价格 ~¥1/百万 token。旧别名 `deepseek-chat`/`deepseek-reasoner` 已弃用。

### Anthropic Provider (`anthropic.py`)
AnthropicLLMProvider。Claude Sonnet/Opus/Haiku，200K 上下文。关键：`_convert_messages()` 将 OpenAI 格式转为 Anthropic 格式（图片用 `base64` source）。

### Seedream Provider (`seedream.py`)
SeedreamImageProvider。中文电商语义最强。支持两种认证：火山引擎 AK/SK（V4 签名）或即梦AI 平台 Key。

### Qwen Provider (`qwen.py`)
QwenLLMProvider。通义千问 VL-Max + Max，DashScope 兼容接口。

### FLUX Provider (`flux.py`)
FluxImageProvider。写实光影最强。三后端优先级：BFL 官方 > Fal.ai > Replicate。Replicate 模式需要轮询等待结果。

## 配置 (`config/models.yaml`)

```yaml
capabilities:
  vision:
    default: openai/gpt-4o
    alternatives: [deepseek/deepseek-v4-flash-vision-exp, anthropic/claude-sonnet-4-20250514, qwen/qwen-vl-max]
    fallback: [mock]
  text:
    default: deepseek/deepseek-v4-flash
    alternatives: [deepseek/deepseek-v4-pro, openai/gpt-4o, anthropic/claude-sonnet-4-20250514, qwen/qwen-max]
    fallback: [mock]
  image:
    default: seedream/seedream-5.0
    alternatives: [openai/dall-e-3, flux/flux.1-dev]
    fallback: [mock]

agent_overrides:
  提示词生成员:
    text: deepseek/deepseek-v4-flash
```

## 端点与模型覆盖（第三方 coding plan / 中转 / 自建网关）

使用非官方端点（coding plan、代理、企业网关）时，官方 base URL 不可用，且其模型 id 通常
不在内置目录中。两项都可在设置页按 Provider 配置，也可直接落文件：

```yaml
# config/providers.yaml（已 gitignore —— 含私有端点，属本地配置）
openai:
  base_url: https://your-coding-plan.example.com/v1
  models: [gpt-5-codex, claude-sonnet-4-5-20250929]
```

| 项 | 规则 |
|----|------|
| 解析顺序 | 环境变量 `<ROUTE>_BASE_URL`（`ECOMM_` 前缀亦可；qwen 兼容 `DASHSCOPE_BASE_URL`）→ `config/providers.yaml` → 内置官方端点（`src/core/config.py::resolve_base_url`） |
| 支持范围 | openai / deepseek / anthropic / qwen（均兼容 OpenAI 或 Anthropic 协议，末尾路径由 Provider 拼接，如 `/chat/completions`、`/messages`）；**seedream / flux 端点固定**（多上游签名），仅模型目录可自定义 |
| 生效方式 | 设置页保存后立即重建 Provider 注册表 + 重载 Agent（无需重启）；env 供给的端点只读（设置页写入被 403 拒绝，避免"改了不生效"） |
| 模型目录 | 自定义模型并入 `model_catalog` 建议列表 → 可在「模型映射」中设为某能力的 default/alternatives/fallback（`provider/model` 形式） |
| 写接口 | `POST /api/settings/providers/{route}`（仅 admin）：`{"base_url": "...", "models": [...]}`；空值表示清除并回落官方端点 |

## 使用方式

```python
from src.providers import get_provider_registry

registry = get_provider_registry()
provider, model = registry.resolve(["vision"], "商品分析员")
result = await provider.chat_with_vision(messages, model=model)
```

## 修改指南

- **新增 Provider** → 创建 `src/providers/{name}.py`，继承 BaseLLMProvider/BaseImageProvider，在 `__init__.py` 注册检测逻辑和延迟加载
- **新增能力** → 在 `capabilities` 字典中添加新键，更新相关 Provider 的 `capabilities` 列表
- **修改模型优先级** → 编辑 `config/models.yaml` 的 `default`/`alternatives` 即可
- **换端点 / 加自定义模型** → 设置页「模型服务商 → 自定义设置（端点与模型）」，或直接写 `config/providers.yaml`；Provider 构造函数用 `resolve_base_url("<route>", "<官方默认>")` 取值
- **所有 Provider 无 Key 运行不崩溃** → 必须返回 `{"error": "...", "detail/hint": "..."}` 结构

## 文+图双条件生图（A50，2026-09-16）

用户要求"生图必须是文＋图，而不是单纯的图生图或文生图"。接口与能力声明：

```python
async def generate(self, prompt, negative_prompt="", size="1024x1024", model="",
                   *, reference_images: list[str] | None = None,   # data: URI 列表（商品身份）
                   options: dict | None = None) -> dict            # 平台参数（watermark 等）
```

| 类属性 | 含义 | 方舟（ark）取值 |
|--------|------|-----------------|
| `supports_reference` | 能否吃参考图 | `True`（单图生单图 / 2-14 张多参考图） |
| `supported_options` | 额外接受的平台参数白名单 | `("watermark", "output_format")` |
| `supports_negative_prompt` | 是否原生支持负面提示词 | `False`（Seedream 不支持 → 调用方把负面约束**折进正向提示词**） |

要点（都是实测教训）：

- **`prompt` 与 `image` 同时入体**：文本负责场景（背景/构图/光影/画幅），参考图负责商品身份
  （品牌文字/包装文案/图案/配色/形制）。此前请求体只有 `model/prompt/n/size/quality`
  —— 模型没见过包装，于是把 `DEFOEBUENA®` 编成了 `NUTRIVA®`。
- **`watermark: false`**：方舟默认会给图打「AI生成」水印（实测产物右下角可见），显式关闭。
- **不支持的参数一律回报 `ignored_params`**：旧 Seedream（AK/SK 签名）与 FLUX 路由不支持参考图，
  被要求时必须回报（与 A41"死配置"同族：静默丢弃 = 用户以为生效了）。方舟白名单之外的键同理。
- **返回值还带** `reference_count` / `request_params`（张数/尺寸/水印/输出格式/prompt 长度，
  **绝不回传 base64 本体**），供产物与审计复盘"这张图到底怎么生成的"。
- **旧签名的自定义 Provider 不会崩**：`ImageGeneratorAgent` 会探测 `generate()` 的签名并按需过滤
  实参，被过滤的条件记进 `generation_params.ignored_params` 与 `image_notes`。
