# Providers — AI 服务商适配层

> 覆盖: `src/providers/__init__.py`, `src/providers/base.py`, `src/providers/mock.py`, `src/providers/openai.py`, `src/providers/deepseek.py`, `src/providers/anthropic.py`, `src/providers/seedream.py`, `src/providers/qwen.py`, `src/providers/flux.py`, `config/models.yaml`

## 功能

Provider 层封装了所有外部 AI 服务的调用细节，提供统一的 LLM/Image Provider 接口。核心职责：

- **能力声明** — 每个 Provider 声明自己能提供的能力（vision / text / image）
- **自动检测** — 启动时扫描环境变量，标记哪些 Provider 可用
- **能力解析** — Agent 声明 `requires: ["vision"]` → ProviderRegistry 根据 config/models.yaml 解析出具体 Provider + Model
- **延迟实例化** — 真实 Provider 首次调用时才初始化，避免启动时检查 API Key

## Provider 全景

| Provider | 能力 | 环境变量 | API 模式 |
|----------|------|---------|---------|
| OpenAI | vision, text, image | `OPENAI_API_KEY` | GPT-4o + DALL-E 3 via `/v1/chat/completions` |
| DeepSeek | text | `DEEPSEEK_API_KEY` | OpenAI 兼容 API via `/v1/chat/completions` |
| Anthropic | vision, text | `ANTHROPIC_API_KEY` | Claude Sonnet/Opus via `/v1/messages` |
| Seedream | image | `SEEDREAM_API_KEY` 或 `VOLCANO_ACCESS_KEY`+`VOLCANO_SECRET_KEY` | 即梦AI 5.0 |
| Qwen | vision, text | `DASHSCOPE_API_KEY` | 通义千问 via DashScope 兼容 API |
| FLUX | image | `BFL_API_KEY` / `FAL_KEY` / `REPLICATE_API_KEY` | 三后端支持 (BFL 官方 / Fal.ai / Replicate) |
| Mock | vision, text, image | 无需 | 模板数据（保健品） |

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
DeepSeekLLMProvider。仅 text，价格 ~¥1/百万 token。

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
    alternatives: [anthropic/claude-sonnet-4-20250514, qwen/qwen-vl-max]
    fallback: [mock]
  text:
    default: openai/gpt-4o
    alternatives: [anthropic/claude-sonnet-4-20250514, deepseek/deepseek-chat, qwen/qwen-max]
    fallback: [mock]
  image:
    default: openai/dall-e-3
    alternatives: [seedream/seedream-5.0, flux/flux.1-dev]
    fallback: [mock]

agent_overrides:
  提示词生成员:
    text: deepseek/deepseek-chat
```

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
- **所有 Provider 无 Key 运行不崩溃** → 必须返回 `{"error": "...", "detail/hint": "..."}` 结构
