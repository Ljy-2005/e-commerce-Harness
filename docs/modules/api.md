# API — FastAPI 接入层

> 覆盖: `src/api/main.py`, `src/api/auth.py`
> 工作流端点细节: `docs/workflow-design.md` §7
> 兼容入口: `src/main.py` 为 `sys.modules` 别名 shim（PRD D4 分层迁移后保留，旧 `uvicorn src.main:app` 命令与 `import src.main` 依旧可用）

## 功能

FastAPI 服务，提供前后端分离架构的后端接口。共 4 组 REST + 2 条 WebSocket：

- **会话组** — 创建/详情/消息/列表/删除/人工决策/群聊插话/A-B 测试
- **设置组** — API Key 持久化 / Agent 参数 / 能力→模型映射（均落盘 + 热重载）
- **工作流组** — 模板画廊/实例化/作业列表/详情/控制/人工决策/一键复刻/批量/导入导出
- **连接器组** — 入站 Webhook 回调（`X-Webhook-Token` 鉴权）
- **WebSocket** — `/ws/sessions/{id}`（群聊直播 + 双向插话）、`/ws/workflows/jobs/{id}`（工作流事件流）
- **CORS** — 白名单来自 `config/default.yaml`（`ECOMM_CORS_ORIGINS` 可覆盖，见 `core.md` 的 `get_cors_origins`）

## 端点概览

### 健康与状态

```
GET /health            → {status, mock_mode}（最小化，防信息泄露）
GET /api/admin/status  → 熔断/限流/Agent/Provider/租户总览（需鉴权）
GET /api/agents        → Agent 及可配置参数（前端配置面板数据源）
```

### 会话组

```
POST   /api/sessions                          # 上传商品图 → 异步启动群聊（租户配额检查 + 图片验证 + 预处理）
GET    /api/sessions                          # 会话列表（租户隔离，按创建时间倒序）
GET    /api/sessions/{id}                     # 完整状态（消息 + 产出物 + 成本）
GET    /api/sessions/{id}/messages?since=N    # 增量拉取消息
POST   /api/sessions/{id}/decision            # 人工审查 approve/retry/reject → resume_after_hitl
POST   /api/sessions/{id}/interject           # M3 群聊插话（Coordinator 下一轮感知）
POST   /api/sessions/{id}/ab-test             # 同会话 A/B 变体对比
GET    /api/sessions/{id}/images/{index}/download  # 下载单张生成图（按落盘序号）
GET    /api/sessions/{id}/export              # 打包导出该会话全部生成图（ZIP）
DELETE /api/sessions/{id}                     # 删除会话及产出物
```

### 设置组（写操作落盘 + 热重载）

```
GET  /api/settings                     # 设置汇总（密钥脱敏 + 生效模型 + Provider 端点/模型 + 能力落点）
POST /api/settings/api-keys            # 保存 API Key → config/secrets.yaml → 重建 Provider 注册表
POST /api/settings/providers/{route}   # 保存 Provider 端点/模型 → config/providers.yaml → 重建注册表
POST /api/settings/providers/{route}/test  # 测试连接（1 次最小调用，验证 Key/端点/模型真可用）
POST /api/settings/agents/{name}       # 更新 Agent 参数 → config/agents/*.yaml → 重载注册表
POST /api/settings/models              # 部分更新能力→模型映射 → config/models.yaml → 重建注册表
POST /api/settings/output              # 设置生成图输出根目录 → config/output.yaml
GET  /api/settings/providers/presets   # 一键添加用的服务商预设（含"已存在"标记）
POST /api/settings/providers/custom    # 新增/覆盖自定义服务商 → config/custom_providers.yaml → 重建注册表
DELETE /api/settings/providers/custom/{route}  # 删除自定义服务商（内置路由不可删）
POST /api/settings/providers/move-credential   # 凭据迁移（放错槽位时一键搬正）
GET  /api/settings/providers/{route}/models    # 拉取该服务商账号可用模型（OpenAI 兼容 /models）
```

`GET /api/settings/providers/{route}/models`（仅 admin）：用已配置凭据调用服务商的
`GET {base_url}/models`，返回 `{ok, base_url, total, models[], applicable{text,vision,image}, detail}`。

`models[]` 每条（**原样保留上游展示信息**；此前只传 4 个字段，界面只能显示原始 id，看着像"怪名字"）：

| 字段 | 含义 |
|------|------|
| `id` / `name` / `version` | 调用用的 id / 上游展示名 / 版本（实测方舟 133/133 条都带 name） |
| `status` | `Shutdown`（已下线）/ `Retiring`（即将下线）/ 空 |
| `input_modalities` / `output_modalities` | 模态（能力归属判定依据） |
| `recommended` / `skip_reason` | 是否进建议 / 不进的原因（已下线、即将下线、专用模型、代码专用） |

`applicable` 只含**可推荐**模型，按上游 `created` **新→旧**排序；一个模型可同时进多个能力
（多模态模型既属视觉也属文本），避免"文本"里只剩纯文本模型。过滤规则（实测用户反馈
"名字有些问题"）：`Shutdown` / `Retiring` 排除；`embedding` / `translation` / `smart-router` /
`character` / `seed3d` / `seedance` / `ocr` 等专用家族排除；`-code-` 代码专用排除
（全量 `models[]` 仍保留并标注原因，便于排查"为什么它不在建议里"）。

仅 OpenAI 兼容路由可用（其他协议 400）；未配凭据直接返回说明；服务商不提供该接口时
返回 404 说明并提示手填；**响应永不回显密钥**。

`POST /api/settings/providers/move-credential`（仅 admin）：body
`{"from_env": "VOLCANO_ACCESS_KEY", "to_env": "ARK_API_KEY"}` —— 把已保存的密钥从
一个槽位搬到另一个（**不回显密钥**），并清空源槽；两个变量名都必须属于已知服务商凭据，
源槽为空时 400。用于实测场景：火山方舟 Key 被填进了旧版签名的 AccessKey 槽。
返回最新设置载荷 + `moved: {from_env, to_env}`。

`GET /api/settings` 的 `provider_routes[]` 新增字段（自定义服务商，用户反馈"无法自己添加"）：

| 字段 | 含义 |
|------|------|
| `custom` | 是否为用户自定义服务商（内置为 false） |
| `kind` | 协议：`openai` / `anthropic` / `volc_cv`（旧版火山签名）/ `flux` |
| `api_key_env` | 自定义服务商的凭据变量名（内置为空串，凭据见 `api_keys[]`） |

`POST /api/settings/providers/custom`（仅 admin）：body
`{route, label, kind, base_url, api_key_env, capabilities[], models[], credential_hint?}`。
校验：route 格式（小写字母开头 2-32 位）与重名（内置/自定义互斥）、kind ∈ {openai, anthropic}、
capabilities 为 text/vision/image 非空子集、base_url 必须 http(s)、api_key_env 为大写变量名、
模型 id 仅可打印 ASCII 且 ≤50 个 —— 任一不满足 400 并给出可读原因。
保存后**该凭据变量立刻进入可保存白名单**（此前 `_ALLOWED_SECRET_KEYS` 是静态集合）。
`DELETE` 仅能删自定义路由（内置返回 404）；两者均需 admin（租户 Key 403）。

`GET /api/settings` 的 `output` 条目（设置页「生成图输出目录」卡片；A23）：

| 字段 | 含义 |
|------|------|
| `dir` | 用户配置值（空 = 用默认目录） |
| `effective_dir` | 生效路径（绝对路径） |
| `source` | `env` / `file` / `default` |
| `env_locked` | 是否由 `ECOMM_OUTPUT_DIR` 提供（此时设置页只读） |
| `writable` / `error` | **真实 mkdir + 探针写入**的结果（盘符不存在/无权限/只读挂载会在这里暴露） |

`POST /api/settings/output`（仅 admin）：body `{"dir": "D:/exports"}`，空字符串 = 回落默认。
拒绝规则：`dir` 非字符串 400；**不可写路径 400 且不写入配置**（避免持久化坏路径）；
`ECOMM_OUTPUT_DIR` 已设置时 403（改环境变量后重启才生效）。

生成图导出端点（A23，均按 `X-Tenant-ID` 隔离，跨租户一律 404）：

| 端点 | 说明 |
|------|------|
| `GET /api/sessions/{id}/images/{index}/download` | 单张下载，序号从 1 开始（非整数 400；未落盘 404）；`Content-Disposition: attachment` |
| `GET /api/sessions/{id}/export` | 整会话 ZIP（`application/zip`）；无落盘文件 404 |

`GET /api/settings` 的 `capabilities[]` 条目（B3-26：新建任务表单的"能力落点"警示来源，
租户脱敏子集同样包含）：

| 字段 | 含义 |
|------|------|
| `capability` | `text` / `vision` / `image` |
| `provider` / `model` | 该能力此刻**实际**解析到的 Provider 与模型（`mock` 表示回落） |
| `is_mock` | 是否回落到 Mock 占位实现（只配 DeepSeek 时 `image` 为 true） |

`GET /api/settings` 的 `provider_routes[]` 条目（设置页「一行一 Provider」的端点与模型来源）：

| 字段 | 含义 |
|------|------|
| `route` / `label` / `capabilities` | 路由 id（内置 7 条 + 自定义）/ 展示名 / 能力 |
| `default_base_url` | 内置官方端点（图像路由为空） |
| `base_url_supported` | 是否允许自定义端点（openai/deepseek/anthropic/qwen/ark 与自定义服务商为 true；seedream/flux 多上游，固定为官方） |
| `effective_base_url` | 当前生效端点（env → 文件 → 官方） |
| `base_url_custom` / `base_url_source` | 是否被覆盖 / 来源（`env` / `file` / `''`） |
| `models` / `official_models` | 自定义模型列表 / 内置官方目录 |
| `custom` / `kind` | 是否用户自定义 / 协议（`openai`/`anthropic`/`volc_cv`/`flux`） |
| `api_key_env` | 自定义服务商的凭据变量名（内置为空串） |
| `deprecated` / `deprecated_hint` | 是否旧版通道 / 引导文案（前端渲染「旧版通道」徽章与横幅） |
| `models_by_capability` | 按能力分组的官方模型（`{text:[…], vision:[…], image:[…]}`）——避免把生图模型建议到文本能力上 |

`POST /api/settings/providers/{route}`（仅 admin）：body `{"base_url": "...", "models": [...]}`，
空值表示清除该项并回落官方端点。拒绝规则：未知路由 404；非 http(s) URL 400；图像路由传端点 400；
模型 id 非可打印 ASCII / 超长 400；**端点由环境变量供给时写入 403**（改设置页不生效，显式拒绝）。
自定义模型会并入 `model_catalog` 供「模型映射」建议列表使用。

`POST /api/settings/providers/{route}/test`（仅 admin，B3-24 测试连接）：body 可选 `{"model": "...", "allow_image": true}`。文本/视觉路由发 1 次最小 chat 调用；
**图像路由默认跳过**（真实生成有费用与数十秒等待，`allow_image=true` 才真跑）；
Mock 路由不发起真实调用（`is_mock=true, skipped=true`）。返回：

| 字段 | 含义 |
|------|------|
| `ok` / `skipped` / `is_mock` | 真实调用是否成功 / 是否未发起调用 / 是否 Mock 路由 |
| `model` / `base_url` | 实际下发的模型 / 生效端点（body.model → 自定义模型 → 能力映射 → **该能力分组的官方模型** → Provider 默认） |
| `latency_ms` | 往返耗时 |
| `detail` / `reason` | 失败时的上游原文（状态码 + 端点 + 响应体，密钥已遮蔽；"模型不可用"类错误会追加一句"鉴权已通过"的说明）/ 跳过原因 |
| `suggested` | 凭据放错槽位时的迁移建议 `{route, env, from_env}`（否则为 `null`） |

**凭据预检**：调用前先判断「没配凭据 / AK-SK 只配一半 / 方舟 Key（`ark-` 前缀）落在旧卡片」，
直接返回可执行说明（不再出现 `LocalProtocolError: Illegal header value b'Bearer '` 这类裸错误）。

**生图分支的尺寸与超时**（A30，实测事故）：尺寸取 `resolve_image_size(provider.default_size)`
（方舟 = `2048x2048`，写死 `1024x1024` 会被方舟以 `image size must be at least 3686400 pixels` 拒绝），
超时用独立的 `PROVIDER_TEST_IMAGE_TIMEOUT_S = 120s`（实测单张约 30s；文本分支仍是 20s）。

连接失败**仍返回 200**（`ok=false` + `detail`），由界面直接展示；未知路由 404，畸形 body 400。

`GET /api/settings` 的 `agent_overrides_issues[]`（A41，模型映射页的失效覆盖告警）：
| 字段 | 含义 |
|------|------|
| `agent` / `capability` | 配置了覆盖的 Agent 与覆盖键 |
| `requires` | 该 Agent 真正需要的能力（来自 `config/agents/*.yaml`） |
| `reason` | 可读原因，如「该 Agent 需要 vision，"text"覆盖不会生效」或「该 Agent 不存在」 |

覆盖键不在 `requires` 里时后端**静默忽略**（用户以为换了模型其实没换），此字段让前端能显式提示；
仓库自带的 `config/models.yaml` 保证该清单为空（有回归测试钉住）。

`GET /api/settings` 的 `chat` 条目 + `POST /api/settings/chat`（仅 admin，B1 会话策略）：

| 字段 | 含义 |
|------|------|
| `max_consecutive_review_failures` | **审查/合规连续未通过 N 次即自动停止会话**（0 = 关闭；默认 2） |
| `max_turns` | 群聊最大轮次（1–50） |
| `session_ttl_hours` | 会话 TTL（小时；保存后立即作用于惰性驱逐） |
| `require_identity_confirm` | 商品身份未确认时是否暂停等用户确认（默认 true） |
| `require_prompt_review` | **出图前是否跑「体检 + 提示词审核优化」**（默认 true，A78） |
| `prompt_aesthetic_threshold` | 审美阈值 0–100（默认 85；低于它的槽位会给改写稿） |
| `prompt_review_max_rounds` | 体检仍有硬伤时打回提示词生成员重写的轮数 0–3（默认 1） |
| `require_prompt_confirm` | 提示词仍不达标时是否暂停等人工确认（默认 false） |

`POST` 为部分更新（写上表字段，其它字段 400），非法值 400，保存即写入 `config/chat.yaml`
（**不改写 `default.yaml`**，注释保留）并生效（无需重启）。前端入口：设置页「🛑 会话策略」。

`GET /api/settings` 的 `api_keys[]` 条目（设置页 provider 行的状态来源）：

| 字段 | 含义 |
|------|------|
| `name` / `env` / `capabilities` | 展示名 / 环境变量名 / 能力描述 |
| `provider` | 该密钥供给的 Provider 路由 id（openai/deepseek/anthropic/seedream/qwen/flux） |
| `configured` | 当前是否已配置（**布尔，不返回任何密钥片段**） |
| `available` | 该路由此刻是否已注册可用（与 `configured` 联合决定状态点：绿=生效中 / 黄=已配置未激活） |
| `source` | `''` 未配置 · `'file'` 由 secrets.yaml 持久化 · `'env'` 进程环境变量供给 |

`POST /api/settings/api-keys` 的拒绝规则（P7）：未知变量 → 400；**环境变量供给的密钥 → 403**
（`secret_key_source() == 'env'`；环境变量优先级最高，设置页保存不会生效，故显式拒绝而非静默失效）。

### 工作流 / 批量 / 记忆 / 审计

```
GET    /api/workflows/templates                     # Skill 库列表
GET    /api/workflows/templates/{name}/export       # M4 导出原始 YAML
POST   /api/workflows/templates/import              # M4 导入（三级校验 + 防覆盖）
POST   /api/workflows/templates/{name}/instantiate  # multipart 多图片输入分发
GET    /api/workflows/jobs                          # 作业列表
GET    /api/workflows/jobs/{id}                     # 详情（步骤 + 事件流）
POST   /api/workflows/jobs/{id}/control             # 手动挡 run_next/pause/resume/retry_step/skip_step/cancel
POST   /api/workflows/jobs/{id}/decision            # 人工审批 approve/retry/reject
POST   /api/workflows/jobs/{id}/replicate           # M3 一键风格复刻
POST   /api/workflows/batches                       # M2 批量（JSON/CSV）
GET    /api/workflows/batches / batches/{id}        # 批量列表/详情
GET    /api/workflows/batches/report                # M5 批量报表（总览/模板成功率/耗时分布/失败原因）
POST   /api/workflows/batches/{id}/control          # pause/resume/cancel/retry_failed
POST   /api/webhooks/workflows/{id}/decision        # M4 入站回调（X-Webhook-Token；未配置 503）

GET    /api/memory/stats / api/memory/recall        # Agent 记忆库
GET    /api/audit                                  # 审计日志查询
```

### WebSocket

```
WS /ws/sessions/{id}        # ?api_key= 鉴权；历史回放 + 实时流 + 用户插话 {type:"chat", content}
WS /ws/workflows/jobs/{id}  # ?api_key= 鉴权；历史事件回放 + 实时事件流 + ping/pong
```

## 鉴权与租户

- **API Key**：`ECOMM_API_KEY` 配置后 AuthMiddleware 全局保护（见 `auth.md`）；WS 用 `?api_key=` 查询参数；**前端自动携带**：`api.js` 统一注入 `X-API-Key` 头 + `wsUrl` 追加 `?api_key=`（Key 在设置页「前端 API Key」处存入 localStorage）
- **租户**：`X-Tenant-ID` 头（默认 `default`）；**未知租户 403**（不再回退 default）；会话/作业/批次/WS（`?tenant=` 参数）均按租户隔离
- **管理面**：`/api/settings/*` 与 `/api/admin/*` 在未配置 `ECOMM_API_KEY` 时仅允许本机访问（`_require_admin_access`）
- **上传防护**：分块读取（超 20MB 即 413）→ `ImageValidator`（格式/大小/base64）→ `ImagePreprocessor`（解压炸弹防护/缩放/压缩），失败即 400

## 全局组件

```
ProviderRegistry    # 环境变量检测 → 可用 Provider + 能力解析
AgentRegistry       # 扫描 config/agents/ → 注册 9 Agent + resolve 模型
SessionManager      # 内存会话存储 + checkpoint 持久化
Broadcaster         # WebSocket 连接管理（会话 + 工作流共用）
JobStore            # 工作流 SQLite（事件溯源）
WorkflowEngine      # 工作流状态机
BatchScheduler      # 批量调度器
```

## 修改指南

- **新增端点** → 在 `src/api/main.py` 添加路由函数（自动被 AuthMiddleware 保护）
- **上传限制调优** → `main.py` 顶部常量（`MAX_UPLOAD_IMAGES` / `PREPROCESS_MAX_PIXELS` 等）
- **调整 CORS** → `config/default.yaml` 的 `app.cors_origins` 或环境变量 `ECOMM_CORS_ORIGINS`
- **WebSocket 消息格式** → `chat/broadcaster.py` + `main.py` 的 ws 端点保持同步

## 平台档案与生图质量策略（A53/A56，2026-09-16）

```
GET  /api/platforms                  # 平台档案（config/platforms.yaml）—— 公开读
POST /api/settings/image             # 保存生图质量策略（仅 admin）
POST /api/settings/image/probe       # 试生成一张 + 返回本地体检（仅 admin，**会花钱**）
```

**`GET /api/platforms`** —— 平台清单与套图槽位的**单一事实来源**。此前平台清单散落 9 处
（提示词 YAML 的静态风格只定义了 4 个平台、`config/agents/prompt_generator.yaml` 用中文名、
6 个 workflow 模板各存一份 options、前端硬编码 6 项），选"拼多多"时送进模型的只有一个裸字符串。
现在：新增平台 = 改 `config/platforms.yaml`（含**拼多多**在内的 10 个平台：别名归一/长宽比/
上传尺寸/主图上限/背景/文字策略/禁止元素/风格提示词/套图槽位），前端选择器与提示词风格块都从
这里取。响应条目：`{slug, label, aliases, aspect, export_size, min_side, max_images, bg,
text_policy, slots[], slot_count, is_default}`。

**`POST /api/settings/image`** —— 部分更新，白名单字段 `size` / `variants` / `slot_candidates` /
`text_strategy`(preserve|blur|none) / `reference_mode`(auto|off) / `max_references`(1-8) /
`watermark`(bool) / `quality{edge_whiteness, watermark_zone_delta, identity_similarity_min,
near_copy_max}` / `platforms{平台: {slots: []}}`；未知字段与非法的值一律 400（**不静默收敛**）。
写入独立的 **`config/image.yaml`**（已 gitignore）—— **绝不改写 `config/models.yaml`**：
`yaml.safe_dump` 会把文件里全部注释丢掉，而那些注释本身就是文档。返回最新的
`GET /api/settings` 载荷（含 `image` 段）。

**`POST /api/settings/image/probe`** —— 用当前策略真调**一张**图并回本地体检，让用户在设置页
验证效果，而不必跑一整轮会话。Body 可选：`route`(默认 ark) / `model` / `reference_image`
(base64 或 data URI) / `prompt` / `background` / `aspect` / `slot_id` / `text_strategy`。
响应：`{ok, route, model, latency_ms, prompt, text_strategy, reference_count, ignored_params,
request_params, reference_notes, image_url, quality{...}}`。缺凭据时**先拦下来**（400 + 可执行
指引），而不是发出一个空 Bearer 头再抛 `Illegal header value b'Bearer '`（实测踩过）。

## 会话事实补充（A68，2026-09-16）

```
POST /api/sessions/{id}/facts      # 补充成分/用法/规格/对比等事实（租户隔离）
```

信息图的文案**只取已确认事实**，包装正面看不到的一律 `blocked`；但用户手上往往就有这些信息
（知道食用方法、想跟旧包装对比）。这个端点把用户输入接进真实会话：

- Body：`{"usage": ["每日 2 粒，飯後服用"], "ingredients": ["水飛薊提取物"]}`
  （值可为字符串或字符串数组）；
- **白名单 9 类来源**：`ingredients / features / selling_points / target_audience /
  scene_suggestions / usage / compare / spec / certifications`；未知键、单条 >60 字、
  每类 >8 条、全为空白 → 400（不静默丢）；
- 写入 `task.product_facts` 并**落盘 checkpoint**（`await sessions.update(..., slim=True)`；
  实测漏 await 只在内存生效、重启即丢），群聊播报一条系统消息，`GET /api/sessions/{id}`
  的 `task` 里可回显；
- 生图员下次出图（`_compose_info_slot(..., task)`）把它当 `user_copy` 取用 → 原本 blocked 的
  信息图变成 `composed`（端到端测试钉住）；
- 前端会话页「✍️ 补充素材」：按 blocked 槽位映射到对应事实类型填表，提交后自动经
  `POST /api/sessions/{id}/interject` 请协调者重新出图（**填了才画，不填不编**）。

## 创建会话与槽位子集（A74，2026-09-18）

```
POST /api/sessions        # 表单字段：product_info / platform / category_hint / mode / slots / files[]
```

`slots`（可选，逗号分隔，如 `main_white,main_selling_point`）= **只出这几张**：
用于最小付费冒烟（2 张 ≈$0.08 而不是整套 10 张 ≈$0.40）与"只重出某几张"。

- 解析在 `_clean_slot_subset`：中英文逗号都支持、去重、**id 只允许字母数字下划线短横**、
  最多 12 个，非法 → 400；
- 落成 `task.slot_override`，出图（`image_gen`）、提示词体检/审美审核（`_review_prompts`）、
  白底体检预期与套图覆盖度**全部只针对该子集**（`normalize_set_plan(only_slots=...)`
  会真的过滤掉其余槽位，并在 `notes` 里说明跳过了哪些）；
- 命令行入口：`python scripts/real_suite_run.py --slots main_white,main_selling_point`，
  另有 `--variants draft,refined`（关闭/开启审美改写各跑一轮做 A/B，≈$0.16）。

## 风格词库（A79-A96，2026-09-18；A97-A107，2026-09-20，用户指定的界面模块）

左栏「记忆库」下面的「🎨 风格词库」页面用的就是这一组端点。契约与实现见
`src/api/main.py` 的「风格词库」区块；改任一端点要同步 `frontend/src/api.js` 与
`frontend/src/pages/Styles.jsx`。

```
GET    /api/style-library                 列表：{builtin[], mine[], stats{...}}（只带封面缩略图）
                                          stats.limits = {max_photos, max_photo_mb, max_total_mb,
                                          vision_batch, max_entries_per_tenant}（**前端不硬编码**）
POST   /api/style-library                 multipart：files[](≤limits.max_photos=20) + name
                                          + applies_to(JSON 串) + as_anchor
                                          → 落照片、建 analyzing 词条、**后台**跑风格分析，立即返回
GET    /api/style-library/{id}            详情（全字段 + 全部缩略图 + **shot_flow/shot_roles**
                                          + photo_count + 用量 + 已剔除项 + 候选名）
PATCH  /api/style-library/{id}            编辑（**内置档案只允许 enabled**，其它字段 403）；
                                          enabled:true → **自动停用同租户其他词条**，
                                          响应带 auto_disabled 名单
DELETE /api/style-library/{id}            删除（连照片目录一起清；回报"曾被子 N 次会话采用"）
POST   /api/style-library/{id}/reanalyze  再分析（可带 {"hint": "更冷一点"}；**会再花钱**）
POST   /api/style-library/{id}/photos     追加照片（只 append：序号不变 → 逐张角色仍对得上；
                                          超上限/超总量 400/413）
DELETE /api/style-library/{id}/photos/{n} 移除第 n 张（**清空 shot_roles** 并回报 cleared_roles）
GET    /api/style-library/preview         零成本预览：?platform=&category=&slot=&entry_id=
                                          → 注入块 + summary.coverage（覆盖差），不调用任何模型
POST   /api/sessions/{id}/style           切换**本次会话**的风格词 {"entry_id": "st_x"}（空串=清除）
```

关键语义：

- **用量前置、金额诚实**：创建/再分析返回的 `estimate` 里 `amount` 在未标定价格时是
  **`null`**（不是 0），并带 `note` 说明；`estimate.calls = ceil(张数 / vision_batch)`
  （20 张 = 2 次调用，**分批 = 多次计费**，`note` 里会写出来）。见本文件「成本金额口径（A94）」；
- **异步分析**：`POST` 只落照片 + 建词条，分析在 `asyncio.create_task(_analyze_style_entry(...))`
  里跑；失败一律写可读原因（`failed` + `error`），**不会永远停在 `analyzing`**
  （超时由 `style_store._reap_interrupted()` 惰性收割，阈值按**批数**放宽：
  `600 + (批数-1)×240` 秒）；
- **照片不进生图链路**：落在 `data/style_library/<id>/`，**不写 `data/inputs/`**，
  也永远不作为生图参考图（静态测试钉住）；
- **编辑会再过一遍事实中立清洗**：命中的品牌/成分/认证/色值片段被剔除，`removed` 如实返回，
  `similar` 给出与已有档案的**包含度**提示（避免建一堆同义词条）；
  槽位角色名（「成分配方图」）**先豁免**——那是系统自己的受控词汇，不是"照片上的事实"；
- **一轮会话一套风格词**：会话一旦定下风格就锁在产物里（`style_refs.locked_entry_id`），
  提示词生成/审核/体检/重跑全用它；改词库的启用项**不影响进行中的会话**，要换必须调
  `POST /api/sessions/{id}/style`；
- **租户隔离**：`mine` 按 `X-Tenant-ID` 过滤（内置档案全局可见）。前端目前不带该头，
  所以界面建的词条都落 `default` 租户（与记忆库一致）。

### 成本金额口径（A94）

用户质疑"模型商的价会来回改，硬编码价格表会误导"。定稿：**用量是事实，金额是估算**。

- 未标定价格的模型：`{"amount": null, "source": "未标定", "basis": "N 张图（价格未标定）"}`；
- 前端 `formatCost()` 是全站唯一金额口径（未标定显示"未标定（N 张图）"，绝不显示 0）；
- 设置页「💰 计价」列出**你实际用过的模型**（`pricing.observed_models()` 从审计聚合），
  填单价或做一次「标定」（用实际花费反推单价）。

