# Agents — 多智能体层

> 覆盖: `src/agents/registry.py`, `src/agents/base.py`, `src/agents/coordinator.py`, `src/agents/analyst.py`, `src/agents/category.py`, `src/agents/prompt_gen.py`, `src/agents/image_gen.py`, `src/agents/reviewer.py`, `src/agents/compliance.py`, `src/agents/post_process.py`, `src/agents/style_analyst.py`, `config/agents/*.yaml`, `config/prompts/*.yaml`

## 功能

Agent 层是系统的"脑"。9 个 Agent 在中心决策者的协调下，像群聊一样协作。

- **Agent 不绑定模型** — 只声明能力需求（`requires: ["vision"]`），模型由配置决定
- **文件即注册** — 新增 Agent = 在 `config/agents/` 新建一个 YAML 文件（`class` 字段指向实现类，注册中心动态导入，零核心代码改动）
- **Coordinator 自动感知** — 系统提示词从 AgentRegistry 动态生成，包含所有已注册 Agent

## Agent 池

| Agent | 能力 | 职责 | System Prompt |
|-------|------|------|---------------|
| 中心决策者 | text | 读群聊历史，决定邀请谁 | `config/prompts/coordinator.yaml` |
| 商品分析员 | vision | 识别品类/材质/卖点/风格 | `config/prompts/analyst.yaml` |
| 品类专项分析员 | vision | 按品类深度分析（保健品/化妆品/食品/3C） | `config/prompts/category.yaml` |
| 提示词生成员 | text | 分析结果→多平台多模型提示词 | `config/prompts/prompt_gen.yaml` |
| 生图员 | image | 调用生图 API 出图（3 张变体） | 无需 Prompt（调用 Image API） |
| 审查员 | vision | 5 维度质量评分 + pass/retry 判定 | `config/prompts/reviewer.yaml` |
| 合规审查员 | vision | 广告法 + 平台规范检查 | `config/prompts/compliance.yaml` |
| 图像后处理员 | local | 去背景 + 增强（纯本地计算） | 无需 Prompt |
| 风格拆解员 | vision | 拆解参考图构图/光影/色调/元素（一键风格复刻） | `config/prompts/style_analyst.yaml` |

## 关键类

### AgentRegistry (`registry.py`)
Agent 注册中心。启动时调用 `load_from_config(provider_registry)`：

```
1. 扫描 config/agents/*.yaml
2. 解析每个文件的 AgentMeta
3. 根据 requires 调用 provider_registry.resolve() 获取 Provider
4. 根据 meta.name 查找对应的 Agent 类 → 实例化 → 注册
```

**Agent 名称 → Python 类映射** (硬编码在 `_create_agent` 中):

| meta.name | 类 |
|-----------|-----|
| 中心决策者 | `CoordinatorAgent` |
| 商品分析员 | `ProductAnalystAgent` |
| 品类专项分析员 | `CategorySpecialistAgent` |
| 提示词生成员 | `PromptGeneratorAgent` |
| 生图员 | `ImageGeneratorAgent` |
| 审查员 | `ReviewerAgent` |
| 合规审查员 | `ComplianceAgent` |
| 图像后处理员 | `PostProcessAgent` |
| 风格拆解员 | `StyleAnalystAgent`（插件化：`_MAP` 未命中时按 YAML `class` 字段动态导入） |

### BaseAgent (`base.py`)
Agent 抽象基类。子类实现 `_execute_impl(task_brief, session) → dict`，自动获得 retry + timeout。

### CoordinatorAgent (`coordinator.py`)
中心决策者。Mock 模式返回预设工作流（8 步），真实模式调用 LLM 决策。

```
decide(session) → dict  # {"action": "invite/done", "agent_name": "...", "task_brief": "..."}
reset()                 # 重置 workfow 索引（新会话时调用）
```

### 业务 Agent (各文件)
每个 Agent 实现 `_execute_impl()`，从 `session` 读取上下文，调用 `self.provider`，返回 dict：

```
analyst.py:     从 task.product_images 读取图片 → vision → ProductAnalysis dict
category.py:    从 artifacts.analysis 读取品类 → 返回品类特化分析
prompt_gen.py:  从 artifacts.analysis 读取分析 → text → ImagePrompts dict
image_gen.py:   从 artifacts.prompts 读取提示词 → image (×3) → images[]
reviewer.py:    从 artifacts.images 读取图片 → vision → ReviewReport dict
compliance.py:  从 artifacts.images + analysis → vision → ComplianceReport dict
post_process.py: 从 artifacts.images 读取 → 本地处理 → images[] (processing_status 更新)
```

**Mock 检测**: 所有 Agent 在 `_execute_impl` 中检查 `isinstance(self.provider, MockLLMProvider)`，如果是 Mock 则直接返回模板数据，绕过 LLM 关键词匹配。

## Agent 配置文件格式

每个 `config/agents/{name}.yaml`:

```yaml
name: 商品分析员
description: 分析商品图片，识别品类、材质、卖点、目标人群、风格约束
version: "1.0.0"
requires: [vision]             # 能力需求（不指定模型）
prompt: prompts/analyst.yaml   # System Prompt 引用
timeout_ms: 30000
retry:
  max_retries: 3
  backoff: exponential
params:                        # 可配置参数（前端据此生成表单）
  - key: detail_level
    label: 分析详细程度
    type: select
    options: [basic, standard, detailed]
    default: standard
```

## System Prompt 格式

每个 `config/prompts/{name}.yaml`:

```yaml
system: |
  你是专业的电商商品分析专家。你需要分析商品图片，输出结构化的分析结果。

  ## 分析维度
  1. 品类识别
  2. 材质/成分
  ...

  ## 输出格式
  输出 JSON: {{...}}
```

## 修改指南

- **新增 Agent** → 在 `config/agents/` 新建 YAML（`class: src.agents.xxx.ClassName` 指向实现类，注册中心动态导入，无需改核心代码；仅当类不在 `_MAP` 时才需要 YAML 提供 `class` 字段），在 `agents/` 创建 Python 文件实现 `_execute_impl`
- **修改 Agent 能力** → 编辑对应 `config/agents/{name}.yaml` 的 `requires` 字段
- **修改 System Prompt** → 编辑对应 `config/prompts/{name}.yaml`
- **修改 Mock 行为** → 编辑对应的 `_mock_*()` 方法或 `src/providers/mock.py` 中的模板常量
- **前后端对接** → `params` 字段声明了前端可配置的参数，前端读取 `/api/agents` 即可生成配置表单
