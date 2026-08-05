# Core — 数据模型 + 状态 + 配置

> 覆盖: `src/core/models.py`, `src/core/state.py`, `src/core/config.py`, `config/default.yaml`

## 功能

Core 层是项目的数据基础，定义了三部分核心能力：

- **数据模型** — 所有 Pydantic v2 模型（10 个），贯穿 Agent 输入/输出、Coordinator 决策、消息格式
- **会话状态** — `SessionState` TypedDict，群聊全局状态，在 ChatEngine 主循环中流转
- **配置加载** — YAML + 环境变量（`ECOMM_` 前缀），自动 Mock 检测

## 关键类/模型

### ProductAnalysis (`models.py`)
Agent 1（商品分析员）的输出。包含品类、成分、特征、目标人群、风格约束、营销方向、合规红线。

```
字段: category, sub_category, dosage_form, ingredients[], features[],
      target_audience{}, style_constraints{}, special_constraints[],
      marketing_angles (MarketingAngles), compliance_notes[], confidence_score
```

### ImagePrompts (`models.py`)
Agent 3（提示词生成员）的输出。包含主图/场景图/社交图提示词和多模型版本。

```
字段: main_image (PromptVariant), scene_images[] (ScenePrompt),
      social_images[] (ScenePrompt), model_variants (ModelVariants)
```

### ReviewReport (`models.py`)
Agent 5（审查员）的输出。5 维度加权评分 + pass/retry/fail 判定。

```
字段: overall_score, dimension_scores{}, top_issues[], top_praises[],
      verdict ("pass"|"retry"|"fail"), needs_human_review, iteration
```

### ComplianceReport (`models.py`)
Agent 6（合规审查员）的输出。

```
字段: passed, risk_level, violations[], warnings[], suggestions[]
```

### CoordinatorDecision (`models.py`)
中心决策者的单轮决策：invite 某 Agent 或 declare done。

```
字段: action ("invite"|"done"), agent_name, task_brief, reasoning
```

### AgentMeta (`models.py`)
从 `config/agents/*.yaml` 加载的 Agent 注册信息。

```
字段: name, description, version, requires[], timeout_ms, retry{}, prompt, params[]
```

### Message (`models.py`)
群聊中的单条消息。

```
字段: id, turn, timestamp, role ("coordinator"|"agent"|"system"),
      sender, action ("invite"|"respond"|"done"|"error"), content{}
```

### SessionState (`state.py`)
群聊全局状态 TypedDict，在 ChatEngine 主循环中流转。

```
字段: session_id, status (RunStatus), created_at, updated_at,
      task{}, messages[], artifacts{}, turn_count, max_turns,
      cost_so_far, error_history[]
```

### RunStatus (`state.py`)
状态机枚举: `CREATED → RUNNING → COMPLETED` 或 `↘ FAILED`

## 配置系统

### 加载器 (`config.py`)

| 函数 | 作用 |
|------|------|
| `load_default_config()` | 加载 `config/default.yaml` |
| `load_models_config()` | 加载 `config/models.yaml` |
| `load_agent_config(name)` | 加载 `config/agents/{name}.yaml` |
| `list_agent_configs()` | 扫描 `config/agents/` 返回所有 Agent 名称 |
| `get_env(key)` | 获取 `ECOMM_{key}` 环境变量 |
| `is_mock_mode()` | 检测 Mock 模式：无 API Key → 自动启用 |

### 配置文件 (`config/default.yaml`)

```
app: name, version, log_level
chat: max_turns (15), session_ttl_hours (24)
harness: retry, circuit_breaker, timeout (按 capability)
cost: budget_usd, warn_threshold
image_source: max_file_size_mb, allowed_formats, min/max_resolution
```

## 使用方式

```python
from src.core.models import ProductAnalysis, ReviewReport
from src.core.state import SessionState, RunStatus
from src.core.config import load_agent_config, list_agent_configs

# 加载 Agent 配置
config = load_agent_config("analyst")
agents = list_agent_configs()  # ['analyst', 'category_specialist', ...]
```

## 修改指南

- **新增数据模型** → 在 `models.py` 添加 Pydantic 类，所有字段需有 `Field(description=...)`
- **新增状态字段** → 在 `state.py` 的 `SessionState` 中添加 key，同步更新 `chat/session.py` 的 `SessionManager.create()`
- **新增配置项** → 先加 `config/default.yaml`，再在 `config.py` 中提供读取函数
