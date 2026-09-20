# 复查报告（第二轮）— 2026-08-23

> 触发："你再检查一下有什么好优化的"
> 方法：4 个独立审计（前端 / 后端 / 测试工程化 / 产品体验）+ 自查，**关键指控逐条实测复核**
> 状态：**A 节 58 项已修并验证**（A1-A8 第一批 + A9-A14 第二批：B0 安全四项 + 工程化两项 + A15-A19 第三批：B1 正确性/资源五项 + A20-A22 第四批：B3 产品体验三项 + A23 第五批：生成图落盘与导出 + A24 第六批：自定义服务商与火山方舟建模 + A25 第七批：方舟接入排错与一键迁移 + A26 第八批：模型 id 校准与「拉取可用模型」+ A27 第九批：人工审批竞态 + A28 第十批：拉取结果命名/过滤与 401 解释 + A29 第十一批：模型映射的可用性可见 + **A30-A42 第十二批：真实会话实测复盘（生图/审查/推理预算/门禁/审计全链路）** + **A43-A45 第十三批：真实端到端验收暴露的 3 项** + **A46-A47 第十四批：用户指定的连续失败止损与轻量落盘**）；B 节为待修清单（按优先级，余 15 项）
> 与第一轮的关系：本文只记录第一轮 65 项（`docs/code-review.md`）之外的新发现，或已修项的**覆盖缺口**

---

## A. 本轮已修（58 项，均有回归测试）

### A1-A8 第一批（2026-08-23 上）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A1 | 高 | **Agent 静默把 Provider 失败换成 Mock 数据**：6 个 Agent 用 `result.get("content", self._mock_xxx())`，Provider 报错只回 `{"error":...}`（无 content）→ 默认值生效，会话照常"成功"、分析恒为保健品模板、审查 verdict=pass。配错 Key/端点/模型时用户完全无感知（`src/agents/{analyst,category,compliance,prompt_gen,reviewer,style_analyst}.py`） | 新增 `BaseAgent._content_or_error`：有 error 原样上报（引擎标记 error、工作流直接 fail），仅 Provider 正常但无 content 时才回落模板 |
| A2 | 高 | **成本链路整体失效**：Provider 返回 `tokens_used=100` 但 Agent 只交回 `content` → `_track_cost` 读到 0 → `session.cost_so_far` 恒 0（实测 tracker 记账 0 次）。连带租户月度预算永不触发、审计 token/$ 恒 0、workflow job 与批量报表金额恒 0 | `_content_or_error` 暂存用量信封；`_track_cost` 用量兜底 + 模型名兜底到本次实际下发模型（不再用路由名）；`_PRICES` 补 v4 三型号 |
| A3 | 高 | **Agent 失败覆盖已成功产物**：`_update_artifacts` 把 `artifacts["images"]`（实测 3 张图）整键替换成 `{"error":...}`，会话仍报 completed | 结果含 error 时直接返回，保留既有产出（错误已通过消息/审计可见） |
| A4 | 高 | **前端 3 处裸 fetch 漏发鉴权头**（`api.js` 的 `getSession`/`submitDecision`/`exportTemplate`）→ 配置 `ECOMM_API_KEY` 后会话详情页 3s 轮询永久 401、HITL 按钮不可用、模板导出必失败 | 三处补 `authHeaders()`（此前审计只修了其余 6 处，属遗漏） |
| A5 | 高 | **租户 Key「保存/轮换」空输入可点 = 静默删除密钥**：disabled 条件写成 `!input && configured === false`，已配置租户空输入时可点 → 提交 `api_key=''` → 后端 `pop` 删除；与 placeholder「留空不修改」相反且无确认 | 空输入禁用；删除改为两步确认（与 Provider 凭据一致） |
| A6 | 高 | **非 ASCII 租户 Key 打穿认证链**：`hmac.compare_digest` 对非 ASCII str 抛 TypeError → 任意错误 Key 的请求全部 500（实测）、该 Key 本身也永远无法认证 | 保存入口按可打印 ASCII 校验（400）；新增 `_safe_compare` 包装比较（非 ASCII 判为不匹配 → 401） |
| A7 | 中高 | **Provider 错误只有状态码**：12 处统一 `f"... API error: {resp.status_code}"` → 401/400(模型不存在)/404(端点路径错) 在界面无法区分，配第三方 coding plan 后无法定位 | 统一 `provider_error()`：状态码 + 生效端点 + 上游响应原文（截断、命中密钥自动遮蔽）；前端原样展示 |
| A8 | 中 | **ProviderCard 编辑草稿被父级刷新静默回滚**：改模型/端点时保存凭据 → `setSettings` → `useEffect` 用 props 覆盖本地编辑 | 基线对比：仅当本地无未保存改动时才采纳服务端新值 |

测试增量：后端 +26（608 全绿）、前端 +7（109 全绿）。新增文件：`tests/test_agents/test_error_propagation.py`、`tests/test_chat/test_cost_and_artifacts.py`。

### A9-A14 第二批（2026-08-23 下：B0 安全四项 + 工程化两项）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A9 | 高 | **`GET /api/settings` 缺管理面守卫**：持租户 Key 即可读全量设置。实测响应体里直接带 `https://ops:SUPERSECRET@internal-gw.example.com/v1`（`provider_routes[].effective_base_url` 可内嵌凭据），另有全租户清单、Agent 系统提示词、自定义端点模型 id；dev 模式任意远程 IP 也可读（同 Key 访问 `/api/settings/tenant-keys` 反而是 403） | 新增 `_tenant_settings_payload()`（脱敏子集：仅 mock_mode / providers / Agent 白名单字段 / 静态模型目录）+ `_require_admin_access`；租户载荷带 `redacted: true`，前端设置页据此显示提示并隐藏管理面区块 |
| A10 | 高 | **`POST /api/workflows/templates/import` 缺守卫**：任意租户 Key 可创建模板，且 `force=true` 可覆盖**全局**内置模板（实测复现并还原探针文件） | 端点首行加 `_require_admin_access(request)`（在任何解析/写入之前）；回归测试同时断言"被拒时全局模板文件字节不变" |
| A11 | 高 | **批量执行中 `retry_step` 让同一 job 并发跑两遍**：`batch.py` 用 `await engine.run(job)` 驱动、不登记 `runtime.task`，`retry_step` 的 cancel→start 保护失效（实测同一 job `step_started` 12 次 / 应为 6，步数与成本翻倍） | `run()` 入口登记 `runtime.runner = current_task()`；`start()` 遇在飞 run 复用它而非另起；`retry_step` 对"批量调度器驱动的在飞 run"**明确拒绝**（400，且在重置步骤之前判定，避免拒绝但状态已改坏） |
| A12 | 中 | **批量把 `cancelled` 当可重试失败 → 取消被推翻**：job 被 cancel 后调度器新建 job 重跑全程并最终记 succeeded（实测 job 数 1→2） | `_process_item` 遇非失败终态（cancelled）跳出重试循环并记死信；批次取消后不再起新一轮；死信统一走 `_mark_dead_letter`（保留 failed 计数语义）。取消项留作死信，可经 `retry_failed` 显式续跑 |
| A13 | 高（工程化） | **E2E 冒烟端口语义危险**：`_stop_children` 无条件 `_kill_port_listeners([8000, 5173])`，`--existing`（文档承诺"复用已在运行的服务"）跑完也照杀；自动模式不预检端口，占用时健康检查命中**别人**的服务 → 误判"就绪"且收尾杀掉它。**本轮即因此无法跑冒烟** | `_stop_children(children, ports)` 只清"本次自己启动"的端口；新增 `_auto_mode_conflict()`：默认端口被占用或 URL 指向非默认端口 → 明确拒绝并给出替代路径。**实测对照**：旧路径 `_kill_port_listeners([8000])` 确实杀掉无关哨兵进程，新逻辑下自动/复用两种模式跑完哨兵均存活 |
| A14 | 高（工程化） | **测试污染真实目录**：后端测试直写 `data/{checkpoints,audit,memory}`（实测 checkpoint 累积 **1698** 个文件）与真实 `config/*.yaml`（仅靠 finally 还原，崩溃即半写） | 新增可重定向根：`ECOMM_DATA_DIR`（checkpoint/审计/记忆/workflow.db）+ `ECOMM_PROJECT_ROOT`（config 基准），四处路径全部改为**使用时解析**（此前是模块级常量/file-relative，import 期就锁死真实目录）；`templates._workflows_dir()` 一并统一（原本与写入侧分叉，重定向后"写得进读不到"）；conftest 会话级 `_isolate_runtime_dirs` 把两者指向 tmp 副本（排除密钥文件）。**实测**：`tests/test_api tests/test_chat` 跑完真实目录增量为 0（checkpoint/audit/memory/workflow.db 全 0，config 哈希不变） |

测试增量（第二批）：后端 +23（`test_admin_guard.py` 6 / `test_batch_safety.py` 4 / `test_e2e_smoke_ports.py` 7 / `test_runtime_isolation.py` 6，全量 631 例全绿）、前端 +2（Settings 租户脱敏视图，111 例全绿）。

### A15-A19 第三批（B1 正确性 / 资源五项）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A15 | 中高 | **输出校验遇字符串分数抛 TypeError → 整轮群聊失败**：`BusinessRuleValidator` 直接 `0 <= output["overall_score"] <= 100`，`{"overall_score": "85"}` → `TypeError: '<=' not supported between 'int' and 'str'`；调用处（engine）无 try。同文件另有两条同类崩溃路径：`category` 非字符串（`.strip()` → AttributeError）、`main_image.prompt` 为 dict（把 dict 当字符串 `strip()`） | 新增 `is_number`（排除 bool）/`is_non_blank_str` 类型判定；completeness 与 business 两处全部改用；`OutputPipeline.validate` 再加兜底 try/except → 保证"校验器只返回结果、绝不抛异常"。**红测试实测**：8 种畸形输出全部从"抛异常"变为 `passed=False` + 可读错误 |
| A16 | 中 | **Agent 记忆并发追加丢条目**：`_append_entry` 无锁（`audit_logger` 早有模块级 `_WRITE_LOCK`），实测 200 并发 `remember()` 只落盘 **183/200**（丢 17） | 模块级 `_MEMORY_WRITE_LOCK`（跨实例共享）+ 先序列化再持锁写；新增两条测试：条目数精确相等、每行都是合法 JSON（半行交错） |
| A17 | 中 | **checkpoint 非原子写**：`open("w")` 截断 + 流式 dump，写入中断/并发会让旧 checkpoint 变成半截 JSON（恢复态静默丢失） | 同目录临时文件 + `fsync` + `os.replace`；失败清理临时文件；写入串行化（Windows 上并发 `os.replace` 会 WinError 5，实测踩到）+ 替换短暂退避重试。红测试用"dump 中途抛 OSError"确定性复现：修复前旧 checkpoint 被顶掉，修复后原文件完好且无 `.tmp` 残留 |
| A18 | 中 | **checkpoint 磁盘无回收**：TTL 只清内存不删磁盘，启动仍要 glob 全目录。实测真实 `data/checkpoints` **1689 个文件 / 56.4MB** | 新增 `cleanup_checkpoints(ttl_hours)`（**与内存 TTL 同口径**：超期且非进行中——created/终态/损坏——即删；`running`/`waiting_human` 与未超期一律保留；遗留 `*.json.tmp` 超期 → 删；TTL≤0 不回收）+ `delete_checkpoint_sync`（会话惰性驱逐时同步回收）+ lifespan 启动清扫。**真实世界验证**：真实 `data/checkpoints` 从 **1689 个文件 / 56.4MB** 降至 **8 个 / 318KB**（首次重启回收 1580 个超期终态；二次重启回收余下 103 个从未启动的 `created` 会话，保留 8 个 24h 内的新会话） |
| A19 | 中低 | **恢复会话的 TTL 失效**：写入侧是 `str(datetime)`，驱逐逻辑 `updated.timestamp() if isinstance(updated, datetime) else 0` → 字符串时间戳一律算 0 → 从 checkpoint 恢复的会话**永不驱逐**（内存永久驻留） | 新增 `parse_timestamp()`（ISO 字符串/datetime/无时区 → epoch 秒，失败返回 None）供 checkpoint 与 session 共用；`_sweep_expired` 改用它。红测试：字符串时间戳的恢复会话修复前 `get()` 仍返回对象，修复后被驱逐且磁盘文件同步回收 |

测试增量（第三批）：后端 +26（`test_output_validation_hardening.py` 14 / `test_checkpoint_durability.py` 8 / `test_memory_concurrency.py` 2 / `test_session_ttl.py` +2），全量 **657 例全绿**；前端 111 例、构建零告警（本批无前端改动）。

### A20-A22 第四批（B3 产品体验三项）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A20 | 高价值 | **缺「测试连接」**：`available` 只表示"环境变量非空"——第三方 coding plan（端点与模型 id 都自填）配好后无法验证是否真的能调通，只能等真正跑图才发现配错 | 新增 `POST /api/settings/providers/{route}/test`（仅 admin）：文本/视觉路由发 1 次最小 chat，回显**实际模型 / 耗时 / 生效端点 / 状态码与上游原文**；图像路由默认跳过（真实生成有费用与等待），body `allow_image=true` 才真跑；Mock 路由如实标 `is_mock` 不发起真实调用；连接失败仍返回 200 + `ok=false`（界面直接展示）。前端 ProviderCard 新增「测试连接」按钮 + 结果行（成功/跳过/失败三态着色），图像路由带「包含生图测试（会产生费用）」勾选 |
| A21 | 高价值 | **失败原因不可见**：审计条目早已落 `error[:500]` 但表格没有「错误」列；会话页 failed 无原因、无跳转。更根本的是 `SessionState.error_history` 定义了却**从没人写**（只在创建时初始化 `[]`） | 引擎新增 `_record_error()`（agent 报错 / 超轮次中止 / 协调者缺失 / 人工拒绝四类写入点，最多保留 20 条），会话页新增「会话失败原因」卡片（类型标签 + Agent + 原文 + 审计深链 `/audit?session=<id>`）；审计页新增**错误列**（单行省略 + title 全文）、**「只看失败（N）」过滤**、**`?session=` 深链**预置过滤条件 |
| A22 | 高价值 | **生图静默回落 Mock 但徽章显示"真实 API"**：只配了 DeepSeek 的用户以为在出真图，跑完才发现是占位图 | `/api/settings`（含租户脱敏子集）新增 `capabilities[]`：能力 → 实际 `provider`/`model`/`is_mock`（解析异常降级为 `is_mock=true`，绝不让设置页 500）。新建任务表单顶部显示能力落点条（文本/视觉/生图各自真实 Provider），生图回落时给出醒目警示 + 设置页入口 |

测试增量（第四批）：后端 +21（`test_provider_connection.py` 13 / `test_capabilities_payload.py` 4 / `test_failure_visibility.py` 4），全量 **678 例全绿**；前端 +18（ProviderCard 5 / Sessions 5 / Audit 4 / Session 3 / Settings 1）达 **129 例**，构建零告警。OpenAPI 快照按既定流程刷新。

### A23 第五批（用户实测反馈：生成图没法设置导出路径）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A23 | 高价值 | **生成图完全没有落盘，也没有任何导出入口**（用户："我发现个问题，里面没法自己设置生成图片的导出的路径"）。取证：`deploy/Dockerfile` 早就 `RUN mkdir -p … output && chown …`、`docker-compose.yml` 挂了 `harness_output:/app/output` 卷，但 **`src/` 里没有任何代码往 `output/` 写**——生成图只以 base64 存在内存/checkpoint，前端用 `<img>` 显示，除模板导出外没有下载端点，`config/default.yaml` 里也没有输出目录这一项 | ① **输出根可配置**：`ECOMM_OUTPUT_DIR` → `config/output.yaml` → `<项目根>/output`（相对路径按项目根解析）；设置页新增「生成图输出目录」卡片，带**真实 mkdir + 探针写入**的可写性判定，env 供给时输入框只读且写入 403，非法路径 400 且**不落盘坏配置**。② **生成即自动落盘**：`{根}/{租户}/{会话ID}/{平台}_{品类}_{序号}.{ext}`——聊天引擎与工作流引擎都接（工作流用 job_id 作会话段）；扩展名按**魔数**判定（Mock 的 SVG 占位图不会被写成 .png）；三种图像来源都支持（内联 base64 / data URI / **远程 URL 自动下载**，真实 Provider 常返 URL）；落盘失败只告警、绝不影响生成任务。③ **下载与导出**：`GET /api/sessions/{id}/images/{index}/download` 单张下载 + `GET /api/sessions/{id}/export` 打包 ZIP（均租户隔离，跨租户 404）。④ 会话页图片 tab 显示落盘相对路径 + 单张「下载」+「导出全部（ZIP）」 |

**顺带修掉一个接缝 bug**：A21 让前端读 `session.error_history`、引擎也确实在写，但 `GET /api/sessions/{id}` **根本没返回这个字段**（两边各自有测试、接缝没人测）——已补字段 + 接缝回归测试。

测试增量（第五批）：后端 +37（`test_image_export.py` 17 / `test_image_export_api.py` 15 / `test_image_export_hook.py` 4 + 工作流 2，另 +1 接缝测试）。前端 +8（Session 4 / Settings 5，含既有用例改名适配）。

### A24 第六批（用户实测反馈：无法添加服务商 + 即梦/火山建模错位）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A24 | 高 | **用户无法自行添加模型服务商**（用户："局限性太大了"）+ **把"即梦"当成服务商**（用户："即梦不是一个模型提供商吧，它是存在于火山引擎里可调用的模型"）。取证：① 路由表 `_PROVIDER_ROUTES`、凭据槽 `_API_KEY_META`、模型目录 `_MODEL_CATALOG`、**可保存密钥白名单 `_ALLOWED_SECRET_KEYS`** 全硬编码在 `src/api/main.py`，可用性检测是 `ProviderRegistry.__init__` 里逐个 `if os.getenv("OPENAI_API_KEY")`，Provider 构造也不可参数化——用户**一个字段都改不了**；② 即梦是字节的消费者产品，Seedream/Seedance 是**模型**，服务商是**火山引擎方舟（Ark）**（OpenAI 兼容端点 + 单 `ARK_API_KEY` + `/images/generations`），而原 `seedream` 路由实际打的是 `visual.volcengineapi.com` 的旧版 AK/SK 签名通道（`req_key=jimeng_t2i_v51`）——双重错位，且只配方舟 Key 的用户生图会静默回落 Mock | ① 新增 `src/providers/routes.py` 作为**统一服务商目录表**（内置 7 条 + 用户自定义；`kind` 决定协议；`key_envs` 任一可用 / `all_key_envs` 必须成对）；Provider 类参数化（api_key/base_url/name/capabilities/label 可注入）；② **自定义服务商**：`config/custom_providers.yaml` + 设置页「➕ 添加服务商」（OpenAI / Anthropic 两种兼容模板 + 智谱/Kimi/硅基流动/OpenRouter/方舟/Ollama 一键预设），保存即热重载，凭据槽与可保存密钥**动态放行**；③ **新增内置 `ark`（火山引擎方舟）**：`ARK_API_KEY`、端点 `https://ark.cn-beijing.volces.com/api/v3`、能力 text/vision/image、模型 `doubao-seedream-4-5-251128` 等；旧 `seedream` 路由降级为「火山视觉智能（旧版 AK/SK 签名）」保留兼容；④ `config/models.yaml` 的生图默认值由旧通道改指 `ark/doubao-seedream-4-5-251128`（旧通道/OpenAI/FLUX 依次回落）；⑤ 新端点 `GET providers/presets`、`POST providers/custom`、`DELETE providers/custom/{route}`（内置不可删、租户 Key 403） |

测试增量（第六批）：后端 +51（`test_custom_providers.py` 29 / `test_custom_providers_api.py` 20 / 既有 `test_settings` 路由集合改为从载荷推导并钉住 `ark`）。前端 +5（Settings 预设填充/禁用/提交/报错/删除）。

### A25 第七批（用户实测：配了方舟 Key 却报 Seedream 凭据错误）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A25 | 高 | 用户："为什么我配置了火山方舟的 apikey 但是测试连接显示 Seedream 需要 VOLCANO_ACCESS_KEY + VOLCANO_SECRET_KEY 或 SEEDREAM_API_KEY"。**实测根因**：方舟 Key（`ark-` 前缀，46 位）被填进旧卡片「火山视觉智能（旧版 AK/SK 签名）」的 `VOLCANO_ACCESS_KEY` 槽 → `ark` 无凭据不可用；旧路由只有半个 AK（缺 SK）按既有约定不可用 → 生图回落 Mock；**测试连接只回内部变量名，没有任何"Key 该放哪儿"的指引**。取证中还发现：无凭据时测试连接会把 httpx 裸错误 `LocalProtocolError: Illegal header value b'Bearer '` 抛给用户；方舟端点没有默认模型，不带 `model` 直接 400 `MissingParameter` | ① **测试连接加凭据预检**（缺失 / AK-SK 只配一半 / `ark-` 前缀落在旧卡片）→ 返回可执行说明 + `suggested{route,env,from_env}`；② **一键迁移** `POST /api/settings/providers/move-credential`（搬到正确槽位 + 清空源槽，不回显密钥）+ 前端按钮；③ 旧路由 `deprecated=true` + 引导横幅（写明"`ark-` 开头的 Key 属于方舟卡片"）；④ 路由表新增 `models_by_capability`，测试连接按「自定义模型 → 模型映射 → **该能力官方模型**」解析（修掉 400 MissingParameter）；⑤ "模型不可用"类 404 追加"鉴权已通过（Key 有效），请开通模型或改用接入点 `ep-…`"说明（实测确认用户 Key 有效，卡在模型未开通）。**真实链路验证**：迁移后 `image → ark/doubao-seedream-4-5-251128`（生图不再回落 Mock），测试连接已通过 Ark 鉴权 |

### A26 第八批（用户实测：模型 id 靠猜 —— `Doubao-Seedream-5.0-lite` 404）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A26 | 高 | 用户填 `Doubao-Seedream-5.0-lite` → 404 `InvalidEndpointOrModel.NotFound`。**用其真实 Key 探测方舟 `GET /api/v3/models`（只读免费）取得事实**：接口可用、返回 131 个模型（带 `status`/`modalities`），**该账号不存在 `Doubao-Seedream-5.0-lite`** —— 方舟 id 形态是「小写 + 短横线 + 日期后缀」，账号生图模型仅 `doubao-seedream-5-0-260128` / `-5-0-pro-260628` / `-4-5-251128` / `-4-0-250828`。**同时暴露 A24 内置默认值就是错的**：`doubao-seed-1-6-250815` 不存在、`doubao-seed-1-6-vision-250815` 已 Retiring（这正是 A25 里那次 404 的真因） | ① **校准内置默认**：`routes.py` 的 ark 模型目录与 `models.yaml` 生图默认改为真实账号校准过的 id（生图默认 `ark/doubao-seedream-5-0-260128`）；② **新增「⬇️ 拉取可用模型」**：`GET /api/settings/providers/{route}/models`（仅 admin、仅 OpenAI 兼容路由、不回显密钥）+ 卡片按钮，返回账号可见模型并按能力分组，点一下填入模型目录 —— 从根上消除"猜 id"；③ **id 形态提示**：模型不可用类 404 除"鉴权已通过"外，当 id 含大写或点号时点明正确形态并指向拉取按钮 |

测试增量（第七、八批）：后端 +25（`test_credential_precheck.py` 14 / `test_provider_model_discovery.py` 11），前端 +7（ProviderCard 迁移 3 + 拉取 4）。

### A27 第九批（全量 flake 追查 → 人工审批竞态）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A27 | 高 | 最后一轮全量出现新失败 `test_reject_fails_job`：单跑通过、`tests/test_workflow` 整目录失败。统计复现：`TestHumanNode` 整类连跑**约 25% 概率失败**（只跑 2 个用例 4/4 通过）→ 是**既有 flake**，本轮新增用例只是加大了负载把它逼出来。**根因在生产代码**：`_run_human` 的 `action` 是每轮新建局部变量，`if not runtime.human_action:` 隐含假设"决策已就绪 ⇒ action 已赋值"；决策若落在「WAITING_HUMAN 落库 → 引擎进入等待」这段窗口内（窗口里有 `_emit` 的落库+广播两个 await），就跳过等待而 `action` 仍为空串 → `(routes).get("", 默认边)` 回落**默认边 = 批准**。症状即"应 FAILED 的作业变 completed"；**对真实用户意味着：点「拒绝」若恰好卡在这几十毫秒里会被静默当成批准** | ① `action = action or runtime.human_action`（消费已到达的决策）；② 消费后清空 `runtime.human_action`——否则同一 job 的后续 human 节点或回跳重跑会复用上一次决策（同类隐患）。回归：新增「决策落在发布窗口内不被丢弃」（用 `store.update_job` 钩子在落库瞬间注入 reject，**去掉修复即复现 completed、加上即 FAILED**，确定性可验证）+「决策必须被消费」；`TestHumanNode` 连跑 6 次全绿 |

### A28 第十批（用户实测："模型拉取有问题，名字有些问题"）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A28 | 中高 | 用户对拉取结果的直觉判断是对的，实测三处缺陷：① 上游 `GET /models` **每条都带 `name`/`version`**（实测 133/133），我的接口只透传 4 个字段 → 界面只能显示原始 id（`doubao-seed-2-1-pro-260915`），看着像"怪名字"；② 只按 `output_modalities` 分类，把**专用/辅助模型混进建议**——`-character-`（角色扮演）、`-translation-`（翻译）、`smart-router`（路由）、`-code-preview-`（代码）出现在"文本/视觉"里；③ 只挡了 `Shutdown`，**13 个 `Retiring`（即将下线）仍在推荐**，点了迟早失效。另有分类缺陷：多模态模型只归"视觉"，导致"文本"里只剩 1 个纯文本模型 | ① `models[]` 透传 `name`/`version`/`status`，新增 `recommended`/`skip_reason`；前端 chip **显示模型名、填入完整 id**、标题带版本、显示"已过滤 N 个"；② `applicable` 只含可推荐项：排除已下线/即将下线/专用家族（embedding/translation/smart-router/character/seed3d/seedance/ocr）/`-code-`；③ 建议按 `created` **新→旧**排序；④ **多模态模型同时进"视觉"与"文本"**。**真实账号复验**：133 个 → 可推荐 28、过滤 105（即将下线 69/已下线 22/专用 13/代码 1）；生图建议变为 `doubao-seedream-5-0-pro`/`-5-0`/`-4-5`/`-4-0`。**同时把 401 做成自解释**：`AuthenticationError「The API key format is incorrect」` 现在点明"鉴权失败而非模型问题"+方舟 Key 形态（`ark-…` 约 46 位）+常见填错项（AK/SK、`ep-…`、别家 Key），与 404 模型提示**互斥**（反向断言钉住） |

### A29 第十一批（用户实测："模型映射拉出来的模型不只有已配置的"）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A29 | 中 | 用户："为什么模型映射模块拉取出来的模型不只有已配置好的模型"。**根因**：映射建议来自后端 `model_catalog`，而它是**平台目录**（`{route: list(spec.models)}`——所有内置服务商的官方模型），与是否配置 Key **无关**；设计意图是"可先配好映射再补 Key"，但**完全没标可用性** —— 这正是更早那次"生图默认值指向未配置的 `seedream/seedream-5.0` → 静默回落 Mock"的土壤。另发现生图建议的判定**写死前缀**（`^(seedream/\|flux/\|openai/dall-e)`）→ 自定义生图服务商的模型进不了生图建议 | ① 建议列表按**可用性排序**（已配置在前）+ 每个 option 带 `label`（已配置 / XXX 未配置）；② 表格上方常驻说明"当前可用：…；未配置：…（选它们的模型会回落 Mock）"；③ **当前映射指向未配置服务商时直接告警**（实测组合"生图 → seedream/seedream-5.0"会被点名），`mock` 除外（永远可用）；④ 生图建议改为按 **`models_by_capability` 分组判定**（路由级能力不够细：openai 同时有 text/image，会把 `gpt-4o` 误判成生图模型），无分组信息时只有纯图像路由才整体归入生图（宁可不建议，也不把文本模型塞进生图）；自定义生图服务商的模型现在也能进建议 |

### A30-A42 第十二批（2026-09-16：真实会话实测复盘 —— 一次 9 轮会话"到处都不对但都没原因"）

> 触发："我执行了一次会话，里面出现了很多问题，你去查询一下问题出在哪里了"
> 证据来源：会话 `e11c464b1ac84502` 的 checkpoint / 审计 / 群聊事件 + **真实 Key 复现**
> （方舟 `images/generations` 400 vs 200 对照、DeepSeek `finish_reason=length` vs 16384 预算对照）

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A30 | 高 | **方舟生图 3 张全空却记成"成功"**：`image_gen.py` 写死 `size="1024x1024"`，而方舟 Seedream 5.0 要求 ≥ 3,686,400 像素 → 复现得 `400 InvalidParameter: image size must be at least 3686400 pixels`；换 `2048x2048` → 200（29.8s 出图）。Agent 又从不检查 `result["error"]` → 落 3 条 `image_url=""` 的空图、审计 `status=ok`、协调者据此宣布"生图已完成" | 路由表新增 `RouteSpec.image_size`（ark=2048x2048）+ `config/models.yaml → capabilities.image.{size,variants}` 可覆盖；`BaseImageProvider.default_size` / 注册表注入 / `resolve_image_size()` 校验（拒绝非法串）；Agent **失败即停 + 原样上报 error**，200 但无图像数据也算失败；单张 120s 独立超时；成功回传 `cost_usd/model_used`；"测试连接"生图分支同样用路由尺寸 + 120s 超时 |
| A31 | 高 | **审查员/合规审查员永远拿不到图**：两者只认 `base64_data`（data URI 还写死 png），而真实 Provider（方舟/DALL·E/FLUX）**只返回 URL**，方舟返回的还是 `.jpeg` → 必然 `NO_IMAGE_ACCESSIBLE`，会话必进人工审查 | 新增 `src/harness/vision_payload.py`：base64（魔数嗅探 mime）/ data URI / **本地落盘文件**（路径穿越防护）/ 远程 URL 下载（超时 + 8MB 上限）四源统一转 content parts；审查员无图时**确定性报错且不调用 LLM**；合规审查员同源复用 |
| A32 | 高 | **推理模型把预算吃光 → 输出空串却算成功**：DeepSeek V4 的思考 token 计入 `max_tokens`，复现 `max_tokens=4096 → reasoning_tokens=4097、content=""、finish_reason=length`；同一 prompt 用 16384 → `stop` + 3470 字正文（思考 10276 tokens，75s）。品类专项分析员因此连续两轮"输出为空" | `RouteSpec.max_tokens`（deepseek=16384，可被 `config/providers.yaml` 覆盖）+ 注册表注入；新增 `src/providers/compat.py` 统一 OpenAI 兼容响应守卫：空内容/截断 → 可读错误，`length` 时**自动升 4 倍预算重试一次**（上限 32768），解析走 `parse_json_loose`；Qwen/Anthropic 同步收口（此前各有同一缺陷） |
| A33 | 高 | **围栏 JSON 让审查门禁静默放行**：模型把结论放进 ```json 围栏 → `json.loads` 失败 → `verdict` 丢失 → 引擎 `result.get("verdict", "pass")` **默认按通过**（既不重试也不转人工）；分析员同样因围栏导致 `confidence_score` 读成 0、白跑一轮 | 新增 `src/providers/json_parse.py`（原文 → 去围栏 → 括号平衡取首个对象，绝不抛异常）；门禁重写：`error` / verdict 非法 / retry 无有效分数 一律转人工并写 `error_history`，只有 `pass` 放行 |
| A34 | 中高 | **超时预算与重试策略双错**：提示词生成员 15s 上限 × `with_retry` 4 次 = 用户白等 **66s**，错误文案还写"超时 (15000ms)"；`config/agents/*.yaml` 的 `timeout_ms/retry` 是**死配置**（只进 AgentMeta，从不写回实例） | 注册表把 `timeout_ms`/`retry` 落回实例；超时**不再重试**（只重试传输类异常，编程错误也不重试）；预算按实测重设（提示词/视觉 150s、决策 90s、生图 420s、后处理 120s），Provider httpx 统一 120s |
| A35 | 中高 | **协调者看不到产物真实状态**：`_build_user_prompt` 只给最近 10 条消息（每条截断 200 字），artifacts 完全不进 prompt → 它"合理地"以为图已生成，逼审查员对着不存在的图评分；`_build_system_prompt` 的**记忆段是死代码**（YAML 有 `system` 时直接 return，实测最终 prompt 里没有"历史成功经验"） | 新增「当前产物状态」段（分析 / 提示词 / 图片**可用性逐张标注** / 审查 verdict / 合规 / 最近失败），历史截断放宽到 400 字；系统提示词加"产物状态是唯一权威依据，图片不可用时不得判定完成"；记忆段改为无条件追加 |
| A36 | 中高 | **记忆库串味**：引擎把历史最佳提示词**追加在简报之后**（权重最高），参考来自别的商品（"护肝胶囊…水飞蓟植物元素环绕"）→ 本次提示词写出"水飞蓟植物叶片"，而该商品成分结论是"不可见/待确认"，协调者还专门禁止臆造成分 | `_inject_memory_reference()`：参考**前置**、简报收尾；限定"仅风格/构图"并显式禁止照搬成分/卖点/品牌/认证；长度上限 120 字；`config/default.yaml → memory.inject_prompt_reference` 可整体关闭；`prompt_gen.yaml` 补"事实边界"硬性条款 |
| A37 | 中 | **`None/100` 与潜在崩溃**：`overall_score=None` 被拼成"审查评分 None/100"（用户可见）；`verdict=="retry"` 且分数为 None 时 `None < 75` 直接 `TypeError`（红测试实测复现） | 分数统一走 `is_number` 判定，非数字显示"未给出分数"；无法判定转人工，不再参与数值比较 |
| A38 | 中 | **审计用量/耗时恒 0**：引擎从 Agent 返回的 content 取 `tokens_used/cost_usd/elapsed_ms`，而用量实际存在 `_last_usage` → 9 条审计全 0（唯一非 0 是超时那条）；生图成本完全不落账 | `BaseAgent.execute(..., stats=...)` 出参（用量/耗时/模型/错误），三条返回路径都填充；引擎用它写审计；`time.perf_counter()` 取代 Windows 上 15.6ms 粒度的 `monotonic()`；生图 Agent 回传 `cost_usd/model_used` → `record_image` 落账 |
| A39 | 中 | **checkpoint 把运行时对象写成 repr 字符串**：`json.dump(default=str)` 把 `_cost_tracker` 落成 `"<CostTracker object at 0x…>"`；恢复后 `tracker.record(...)` 抛 `AttributeError`（被吞）→ **该会话从此不再记账** | `_serializable_only()` 剔除运行时键与不可序列化值（告警、不留内存地址）；`CostTracker.from_session()` 按 `cost_so_far` 重建/自愈，`_track_cost` 与启动恢复路径都走它 |
| A40 | 中 | **失败输出污染产物 + 陈旧告警**：专项分析员的空输出 `{"text": ""}` 被 merge 进 `analysis`；第一轮的 `_low_confidence_warning: true` 在第二轮分析正常（置信度 72）后仍留着 | analysis 合并只取"有效载荷"（跳过空值与 `_` 前缀），元字段（校验问题/低置信度）以本轮为准，过期的清掉 |
| A41 | 中 | **死配置与失效覆盖**：`config/agents/*.yaml` 的 timeout/retry 不生效（A34 已修）；`models.yaml` 给"审查员/品类专项分析员/合规审查员"写的 `text:` 覆盖与其 `requires=[vision]` 不符 → 静默忽略（用户以为换了模型）；「图像后处理员」（requires=local，纯本地处理）也挂了一条 `vision:` 覆盖 | `resolve()` 对不匹配覆盖记 warning（同组合只报一次）+ `/api/settings` 暴露 `agent_overrides_issues`；修正自带配置；前端覆盖行**按 requires 过滤能力下拉并自动纠正**，失效行标"不生效"，后端清单在页面上告警 |
| A42 | 中 | **前端把失败展示成"正常但没图"**：图格只写"无图片数据 / 模型: ark / 状态: raw"，下载按钮照常可点；审查页只有 `text` 时整页空白，未评分与阻断原因无从得知 | 图格：无数据 → 红色横幅 + "生成失败"徽标 + 禁用下载；审查页：判定徽标 + "未给出分数" + 阻断原因 + 错误提示 + **渲染审查原文**；维度为空显示 `—` |

测试增量（第十二批）：后端 **+50**（image_gen +6、config +15、provider_connection +2、
vision_payload +12、vision_image_access +6、json_parse +19、output_budget +9、
timeout_and_retry +5、memory_injection +7、audit_usage +5、cost_and_artifacts +4、
checkpoint +8、coordinator +3、settings +3、new_providers +5）；前端 **+7**（映射能力校验 4、
失败可见性 3）。全量 **921 passed / 10 deselected**（0 失败），前端 **162 passed** +
`npm run build` 干净。

### A43-A45 第十三批（2026-09-16：真实会话端到端验收暴露的 3 项）

> 触发：用户批准真实方舟验收。会话 `72d5ef86831c4f99`（真实 Key、真实生图）跑完 生图→审查，
> 3 张 `doubao-seedream-5-0-260128` @2048×2048 落盘 `output/default/72d5ef86831c4f99/`（176/185/164 KB），
> 审查员**真的读到了图**并逐张给出评分（指出生成图里被臆造的 `NUTRIVA®` 品牌、缺失的 GMP 条、AI 水印）。
> 验收同时暴露 3 个新问题：

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A43 | 中高 | **逐变体审查结果被门禁判为"无法解析"**：协调者要求"对每张图输出评分"，审查员于是返回 `{"results":[{variant, overall_score, verdict, …}, …]}` —— 顶层没有 `overall_score`/`verdict` → A33 门禁（正确地）拒绝放行并转人工，但审查内容其实是有效的，用户看到的是"审查结果无法解析"。另：`verdict="fail"` 此前也不拦截，会静默继续跑完 | 新增 `src/harness/review_normalize.py`：无合法顶层 verdict 且存在 `results[]` 时汇总（分数取平均、判定取**最严重** fail>retry>pass、议题去重合并、维度取平均、需人工任一为真），保留逐变体明细；Reviewer Agent 与引擎门禁都调用（幂等）；`config/prompts/reviewer.yaml` 追加"多图也必须给顶层判定"；门禁新增 `fail` → 转人工 |
| A44 | 中 | **3 张图只按 1 张计费**：审计行 `cost_usd=0.12`（Agent 汇总正确），会话 `cost_so_far` 只涨 0.04 —— `_track_cost` 调 `record_image(count=1)` 用定价表重算，既丢张数也丢 Provider 回报的金额 | `record_image(..., amount=)` 支持按实际总金额入账；`_track_cost` 传 `count=len(images)` + `amount=cost`（测试锁定 3 张 = 0.12） |
| A45 | 中 | **重启后历史会话从列表里消失**：会话列表来自内存，而启动恢复只恢复**非终态** checkpoint → 每次重启，completed/failed 会话在 UI 上"不见了"（磁盘文件还在）。实测：本轮重启后用户原始会话 `e11c464b1ac84502` 从列表消失（**由本次排查亲自踩到**） | lifespan 恢复**全部** checkpoint（终态保留原状态，非终态标记为非正常结束），实测重启后两个历史会话都在列表里 |

**验收结论（真实链路）**：A30（3 张真图 + 落盘 + 尺寸 2048×2048）、A31（审查员与合规审查员
都拿到了图；合规判定 `passed=false / risk=high` 并点出臆造品牌）、A32（全程无空输出，
调用耗时 19–102s 均在预算内）、A35（协调者依据新「产物状态」段对合规失败作出反应，
其任务简报里明确写了"严禁照搬历史配方（如水飞蓟/奶蓟草）"）、A36（最终主图提示词**不含**
"水飞蓟"）、A37（HITL 文案为"审查未给出分数…"，不再是 `None/100`）、A38（审计每行都有真实
tokens/cost/耗时：分析 19.1s/4223tok、专项 39.3s+56.5s、提示词 34.4s/15383tok、
生图 101.7s/$0.12、审查 24.7s/15819tok）全部生效。

**真实会话暴露的后续风险（已由用户指定并在 A46/A47 落地）**：
① ~~协调者会在合规失败后反复重生成图片（每轮约 ¥1），会话级预算只告警不拦截~~ → ✅ A46
「连续失败即停」（可设置）；
② ~~checkpoint 只在人工暂停/终态与压缩前落盘，强停会丢整段轨迹~~ → ✅ A47 每步轻量快照
（3.5MB → 22KB 量级）。

### A46-A47 第十四批（2026-09-16：用户指定的两项止损与持久化改造）

> 触发：用户在看到 A43-A45 的验收结论后指定——① 加可设置的「审查/合规连续失败 N 次即停」；
> ② 讲清并解决 checkpoint 落盘粒度问题。

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A46 | 中高 | **没有质量止损线**：合规判定 `passed=false` 后协调者会反复重新生成图片（真实会话里连跑多轮，每轮 3 张 ≈ ¥1），而 `cost_budget_usd` 只告警不拦截、`max_turns=15` 又太远 —— 只能靠人盯着手动停服止损（本次即如此） | 新增 `config/default.yaml → chat.max_consecutive_review_failures`（默认 2，**0 = 关闭**）+ 引擎 `_quality_guard()`：审查 `verdict ∈ {retry,fail}`/报错、合规 `passed=false`/报错 各计 1 次，任一通过清零，达到阈值 → 会话 `failed` + `error_history(kind=abort)` + 群聊系统消息说明原因与调整方式；人工 approve/retry 后清零。**可设置**：设置页新增「🛑 会话策略」卡片 + `POST /api/settings/chat`，写入 `config/chat.yaml`（不动 default.yaml，注释不丢；该文件已 gitignore） |
| A47 | 中 | **checkpoint 落盘粒度太粗**：只在人工暂停/终态/上下文压缩前写 → 长会话中途崩溃/强停会丢掉自上次落盘以来的**整段群聊轨迹**（实测强停后恢复只剩 HITL 那 6 轮）。直接"每轮全量落盘"不可行：checkpoint 里 `task.product_images` 是用户上传图的 base64，实测某会话 **3541KB（上传图占 3400KB）**，每轮写等于写放大百倍 | `save_checkpoint(..., slim=True)` / `SessionManager.update(..., slim=True)`：**每个 Agent 步骤后写轻量快照**（剔掉 `task.{product_images,reference_images}`），完整快照仍保留在人工暂停/终态/压缩前。实测（2.6MB 上传图）：运行中 7 次落盘 **5.7 → 34.8KB（均值 22KB）**，收尾完整快照 2574KB → **缩小约 117 倍**；崩溃最多丢当前 1 个 Agent 步骤 |

测试增量（第十四批）：后端 **+22**（`test_chat/test_quality_guard.py` 8、`test_api/test_settings.py`
10、`test_harness/test_checkpoint_slim.py` 4）；前端 **+3**（设置页会话策略卡片）。
全量 **960 passed / 10 deselected**（0 失败），前端 **165 passed** + `npm run build` 干净。

### A48-A58 第十五批（2026-09-16：「成图质量」——用户四点反馈 + 一次硬编码追问）

> 触发：用户看了真实会话的产物后连续提出——
> ① "产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者提醒，这是一个很大的缺失"；
> ② "你要保证在生图的过程中要文＋图生图，而不是单纯的图生图或者文生图"；
> ③ "生成的不是一套可直接上传的套图，而只是生成了套图中的一张的多张选择，这不符合生产"；
> ④ "这串代码里将商品品牌什么的，好像是写死在代码里了？那这不会直接影响到我生产其他牌子的商品吗"；
> ⑤ "我可能还会做拼多多的商品图，他们的风格也要写上"。

**根因链（全部读码/实测确认，非推测）**

```
上传原图（PNG 2048×2048 2.6MB，品牌 DEFOEBUENA®）
  → 分析员 schema 里没有品牌/商品名/包装文字字段（10 个字段全是品类/成分/卖点）
  → 提示词生成员按平台风格静态清单（只定义了 4 个平台）自由发挥
  → 生图员只取 main_image.prompt，连打 variants(=3) 次
  → 请求体只有 model/prompt/n/size/quality：**参考图从未进入生图环节**
  → 模型没见过包装，凭文字想象 → 臆造 NUTRIVA®、字形乱码、水印、背景非纯白
  → 审查员只收到生成图（没有原图）→ "商品还原度"无基准，只能靠常识猜
```

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A48 | **高** | **全链路没有商品事实基准**：`config/prompts/analyst.yaml` 的字段里没有品牌、商品名，也没有任何包装文字转录；`artifacts` 里没有"商品身份"这个概念 → 下游提示词/生图/审查/合规全程靠模型自由发挥（这正是臆造品牌的土壤） | 分析员新增 `product_identity`（brand/product_name/spec/certifications/package_form/confidence/evidence/**source/derived_from**）+ `visible_text`（包装文字逐字转录，含繁体原文与"哪处看不清"）+ `identity_status`；提示词硬性要求"必须尽力识别品牌与商品名，不得用'未知'带过，严禁按品类常识推断"。引擎落成 **`artifacts.product_identity` 身份卡**并在群聊**醒目播报**（`✅ 商品身份：品牌 \`DEFOEBUENA®\` ｜ 品名 \`金裝強力肝迅康\` ｜ 规格 \`60's\``）；`_inject_identity_card()` 把身份卡**前置**进提示词/生图/审查/合规简报；未确认时**默认暂停**等用户确认（`chat.require_identity_confirm`，识别成功则不打扰；`source ∈ {mock,none}` 只提醒不拦，否则演示数据会让每条 Mock 会话都卡住） |
| A49 | **高** | **交付物不是套图**：`image_gen` 只取 `artifacts.prompts.main_image.prompt`，用**同一个提示词**连打 `variants`(=3) 次 —— 产出的是"套图里某一张的三个候选"；`prompt_gen` 已经生成的 `scene_images`/`social_images` 全被丢掉；连 `config/workflows/white_bg_suite.yaml` 也只调用一次生图员（"套装"只存在于模板描述文字里） | 提示词生成员输出 **`set_plan.slots`**（每槽一张：`main_white`/`main_selling_point`/`main_scene`/`main_detail`/`main_spec`…，含 composition/background/text_in_image/uses_reference/aspect）；`harness/set_plan.py` 规范化（去重/缺提示词跳过/按平台 `max_images` 截断/缺 slot_id 按平台槽位补位）+ **`set_plan_coverage()` 覆盖度**；生图员**按槽位出图**（`variants` 仅用于旧路径，新路径用 `slot_candidates`，默认每槽 1 张）；落盘名 `{平台}_{品类}_{槽位}_{序号}.ext`；审查/协调者/前端全部按套图组织（协调者产物状态直接列"缺哪些槽位"）。旧路径（无 set_plan）保持历史行为并有测试钉住 |
| A50 | **高** | **生图是纯文生图**：`OpenAIImageProvider.generate()` 的请求体只有 `model/prompt/n/size/quality`，**没有 `image` 字段**；`ImageGeneratorAgent` 也从不读 `task.product_images` —— 而方舟 Seedream 本身支持参考图（单图生单图/多参考图），能力有、没用上 | `BaseImageProvider.generate(..., *, reference_images=None, options=None)` + `supports_reference`/`supported_options` 能力声明；方舟按白名单把 `prompt` + `image:[data URI]` **同时**入体（文负责场景、图负责身份），并支持 `watermark:false`（**实测出图右下角有「AI生成」水印**）与 `output_format`；**不支持的参数一律回报 `ignored_params`**（旧 Seedream AK/SK 与 FLUX 路由也一样），禁止静默丢弃；`generation_params` 记录 `reference_count/watermark/text_strategy/ignored_params/request_params` 供复盘。另加**签名探测**：旧签名的自定义 Provider 自动降级并记录"哪些条件没送上去"，不抛看不懂的 TypeError |
| A51 | 中高 | **参考图不持久**：上传原图只活在内存与**完整** checkpoint 里，而轻量快照会剔除 `task.product_images`（base64 太占空间）→ 崩溃恢复/重启后参考图消失，i2i **静默退化成纯文生图**（与 A47 直接相关） | 新 `harness/reference_images.py`：裸 base64/data URI → 字节 → **魔数嗅探 MIME** → 超 4MB 自动降采样（实测上传是 2.6MB PNG，转 data URI ≈3.5MB）→ 张数上限（默认 4）；失败留可读说明不抛异常。上传原图**落盘** `{输出根}/{租户}/{会话}/inputs/upload_N.ext`（元数据写 `task.input_files`），参考图解析**磁盘回退** → 重启后仍能取到 |
| A52 | **高** | **代码里写死了具体商品事实**（用户直接追问）：`MOCK_ANALYSIS` 里是整套"保健品 / 护肝类 / 水飞蓟提取物 / 蓝帽认证 / 25-50 岁 / 解酒护肝"；而 `ProviderRegistry.resolve()` 第 4 步**任何能力没有可用真实 Provider 时静默回落 mock**（`base.py::_content_or_error` 还有一条回落通道）→ 视觉服务不可用（Key 失效/模型下架/额度耗尽）时，**用户那个商品会被套上这套事实，界面还显示成功**。同类先例：A36（记忆库的"水飞蓟"被抄进成分未确认的另一款商品） | ① `MOCK_ANALYSIS`/`MOCK_PROMPTS` **去掉全部具体商品事实**（品牌/品名/成分/认证留空，字段名不变），带 `is_mock: true` + `product_identity.source = "mock"`；② mock/回落产出的分析**身份卡永远 `uncertain`**，群聊与产物标注"⚠️ 演示数据，非本次商品"；③ 身份卡**只由 vision/用户确认产生**，`_has_identity_card()` 阻止品类专项分析员用裸 `product_identity` 覆盖（实测被覆盖成没有 `status` 的数据，下游失去判断依据）；④ 未确认时提示词**禁止出现任何品牌/品名/成分文字**，文字区域一律干净虚化 |
| A53 | 中高 | **平台清单散落 9 处、且拼多多没有风格定义**（用户点名要加）：`config/prompts/prompt_gen.yaml` 的静态风格清单只有 淘宝/小红书/抖音/Amazon；`config/agents/prompt_generator.yaml` 用的是**中文名**（`[淘宝, 天猫, 京东, 拼多多, …]`）而其余 8 处是 slug；6 个 workflow 模板各存一份 options；前端 `Sessions.jsx` 硬编码 6 项 → 今天选"拼多多"，送进模型的只有一个裸字符串，风格全靠模型猜 | 新 `config/platforms.yaml`（**单一事实来源**）+ `src/core/platforms.py`：10 个平台（含**拼多多**）各有 label/别名/长宽比/上传尺寸/主图上限/背景/文字策略/禁止元素/**风格提示词**/套图槽位；别名归一（`拼多多`/`pdd`/`PDD` → `pinduoduo`，顺带合并中英文两套命名）；`platform_style_block()` 渲染进提示词；**未登记平台显式报"未登记"，不得臆造规范**。空出 `default_platform`，新增平台=改配置（前端选择器改走 `GET /api/platforms`）。拼多多关键规则来自公开规范汇编：1:1、推荐 800×800、最低 480、最多 10 张、**白底/活动主图须纯白且"图片上不添加任何文字"**（与本项目的"模型不画字、后期贴图"策略天然一致） |
| A54 | 中高 | **质量问题全靠视觉模型主观判断**，没有客观、可回归的指标；用户又明确要求"不能退化成单纯的图生图或文生图"——两种退化都需要可检测 | 新 `harness/image_quality.py`（零成本、确定性）：背景白度（边缘均值/最小值）、**水印区检测**（右下 70–100%×88–100% 与边缘亮度差）、主体占比、清晰度；与参考图比对给出 **`identity_similarity`**（主体区域颜色分布+结构相关）与 **`is_near_copy`**（整图高度相似**且**背景未换）。**用真实产物回放验证**（不花钱）：三张图边缘均值 240/238/235（均 <250，非纯白）、右下区 −30.7/−2.1/−27.7（水印）、身份相似度 0.29/0.25/0.38（全部低于 0.45 阈值）—— **本次事故在本地就能被提前发现**。引擎在出图后写 `artifacts.quality_report` 并播报问题（缺槽位、疑似水印、身份丢失、疑似复制） |
| A55 | 中高 | **审查员没有比对基准**：`reviewer.py` 只把 `artifacts.images` 送进模型，"商品还原度"维度无从判断（真实会话里它靠常识猜中了被臆造的 `NUTRIVA®`，但不可靠）；合规审查同理 | 审查员/合规审查员改为送 **图一（用户上传的真实商品图）+ 生成图**，header 明确"还原度以图一为基准"；审查提示词新增**硬性比对清单** `fidelity_findings`（品牌文字/品名/规格/认证/图案配色/形制 逐项 same/different/missing/unreadable），并规定"臆造图一中不存在的品牌 = 直接 fail"、"按策略虚化的文字不算 fail"；同时把本地体检数值作为客观锚点附给模型 |
| A56 | 中 | **策略不可设置 / 无法自验**（用户此前反复反馈"没法自己设置"）：文字策略、参考图模式、水印、体检阈值都只存在于代码里；想验证改动只能跑整轮会话（≈¥1） | 新 `config/image.yaml`（UI 写入，**不改写 `config/models.yaml`**：`yaml.safe_dump` 会丢掉文件里全部注释，而那些注释就是文档）+ `image_settings()/save_image_settings()`（白名单校验、非法值报错不静默收敛）；`GET /api/settings` 增 `image` 段；新端点 `POST /api/settings/image`、`GET /api/platforms`、**`POST /api/settings/image/probe`（试生成一张 + 返回本地体检；缺凭据先拦下，而不是抛 `Illegal header value b'Bearer '`）**；设置页「🖼️ 生图质量策略」卡片；会话页身份卡/套图完成度/体检徽章/槽位徽章；平台选择器改接口驱动 |
| A57 | 中 | **顺带发现的静默开关 bug**：`chat_settings()` 用 `merged.setdefault(key, defaults.get(key))` 兜底，`default.yaml` 里没有的键会得到 `None` → `_normalize_chat_policy` 的 `bool(None)` 让新开关**静默变成 False**。作者实测：`require_identity_confirm` 首次接入时"默认开启"实际为关闭，身份门禁根本没生效 | 兜底只在默认值**存在**时写入；新增 `_as_switch()` 三态布尔（None=未设置→默认值，字符串按 true/false 解析），`default.yaml` 补上 `require_identity_confirm: true` 与说明 |
| A58 | 中 | **E2E 冒烟脚本跑不起来（预存在问题）**：脚本的凭据清空清单是硬编码的 10 个变量，**写于方舟路由之前，漏了 `ARK_API_KEY`** → 用户保存过方舟 Key 后 `config/secrets.yaml` 会把真实 Key 注入子进程 → 确定性 Mock 预检拒绝运行（预检本身正确，但脚本从此永远失败）。与 A14 批次 conftest 踩的是同一类坑 | `_provider_key_envs()` 改为「硬编码清单 ∪ **`config/secrets.yaml` 的全部键**」—— 从注入源派生，以后再加服务商也不会漏。E2E 另新增 6 条 S1 断言：身份卡存在、套图编排存在、套图覆盖完整、每张图带槽位、落盘名含槽位、生图参数可复盘（体检在 Mock 下因 SVG 占位图无法解码，断言"通道确实跑了"） |

**测试增量（第十五批）**：后端 **+230**（960 → **1190**），新增 14 个测试文件：`test_core/test_platforms.py`(18)、`test_core/test_image_options.py`(18)、
`test_harness/test_product_identity.py`(15)、`test_agents/test_analyst_identity.py`(9)、
`test_agents/test_prompt_set_plan.py`(20)、`test_providers/test_image_reference.py`(12)、
`test_harness/test_reference_images.py`(15)、`test_agents/test_image_gen_slots.py`(18)、
`test_harness/test_image_export_slots.py`(5)、`test_harness/test_input_export.py`(13)、
`test_api/test_session_inputs.py`(3)、`test_harness/test_image_quality.py`(18)、
`test_agents/test_reviewer_reference.py`(5)、`test_chat/test_identity_merge.py`(4)、
`test_chat/test_identity_gate.py`(13)、`test_agents/test_coordinator_status.py`(11)、
`test_api/test_settings_image.py`(16)，另更新既有用例以符合新契约（mock 中性化 5 处、
`image_options` 契约扩展 1 处、`_mock_image` 签名 1 处）。前端 **+11**（165 → **176**）。
全量 **1190 passed / 10 deselected**（0 失败），前端 **176 passed**，`npm run build` 干净，
E2E 冒烟 **7/7** 通过。真实出图验证（Phase 6）按计划**先停等用户点头**（预计 ¥0.2 起）。

### A59-A60 第十六批（2026-09-16：真机验证 —— 1 张探针 + 1 轮真实套图）

> 触发：用户批准"开始"执行 Phase 6。`scripts/quality_probe.py` 先出 1 张（≈¥0.2），
> 通过后再跑一整轮真实会话（拼多多 5 张套图，≈¥2.9）。

**探针结果（1 张，29.5s，$0.04）——本轮改动逐条被真机确认**：

| 验收项 | 结果 |
|--------|------|
| 参考图真的入体 | ✅ `reference_count=1`，`ignored_params=[]`，`request_params.reference_bytes=3,481,954`（3.5MB data URI 被上游接受） |
| `watermark:false` 生效 | ✅ 右下区偏移 **−0.28**（事故图是 −30.7 / −27.7 → 视觉模型读出「AI生成」） |
| 背景纯白 | ✅ 边缘均值 **250.75**（事故图 240 / 238 / 235） |
| 商品身份保住 | ✅ 相似度 **0.5648**（事故图 0.29 / 0.25 / 0.38） |
| 不是复制原图 | ✅ `is_near_copy=false`（整图相似度 0.63，背景变化 41.8） |
| **包装文字逐字一致** | ✅ 视觉模型逐项比对：品牌 `德國 樂美寶 DEFOEBUENA`、繁体品名、`60's`、`德國GMP優質產品`、图案配色 **全部 same**，`verdict=pass`（**上次这三处被编成了 `NUTRIVA®`**） |

**真实会话结果（拼多多，5 张套图 + 审查 + 合规，8 轮，$0.4078）**：

- 身份识别 **成功**：`confirmed` / 品牌 `德國樂美寶® / DEFOEBUENA` / 品名 `金裝 強力 肝迅康（升級版）` /
  规格 `60's` / 认证 `德國GMP優質產品` / `source=vision` / `confidence=0.86` → 身份门禁**未拦截**，
  流程直接继续（A48 真机生效）。
- 套图编排 **完整**：5 个槽位（main_white/selling_point/scene/detail/spec）全覆盖，
  文件名 `pinduoduo_保健品_膳食補充劑_main_white_1.jpg`（A49 真机生效）。
- **每张图 `reference_count=1`**（文+图双条件在整轮里都成立，A50/A51 生效）；
  唯一被忽略的参数是 `negative_prompt`（方舟不支持，已折进正向提示词，属实情上报）。
- 审查员**拿到了原图**并逐项列出 10+ 条 `fidelity_findings`：品牌/品名/规格/认证/图案配色
  全部 `same`；同时**独立指出**白底图丢失了包装中央的肝臟解剖圖与 6 条英文引线标注
  （与本地体检的 `identity_lost` 判定一致）、背景未达纯白。
- 合规未跑到（审查 `verdict=retry` → 转人工），会话停在 `waiting_human` 等用户决策。

**真机暴露的 4 项缺陷（全部已修 + 回归测试）**：

| # | 严重度 | 问题（真机证据） | 修复 |
|---|--------|------------------|------|
| A59 | 中高 | **体检误报把协调者带偏**：`main_scene`（场景图，边缘 200）与 `main_detail`（特写图，主体占比 0.555、边缘最暗像素 48）被判"疑似水印"；`identity_lost` 误报 4 张（场景/特写本来就换背景换构图）。协调者据此**白跑一轮重生成**（$0.2 → $0.4） | 三类判别修正：① 水印检测只在"背景接近白色（≥240）**且**最外圈最暗像素 ≥200（商品没压到画面边缘）**且** 右下角深色占比 <40%（那一角是内容不是叠加）"时执行，不适用则报 `watermark_checked=false` + 原因；② `identity_lost` 只在生成图同样满足上述"浅色平背景"条件时才判，否则记 `identity_compared=false` + 说明（相似度仍给出供参考）；③ **白底合规按槽位意图判定**（`expect_white`，来自 `set_plan` 里该槽位的 `background`），场景图不再被报"背景不是纯白"。**复核**：上次事故的三张图真阳性全部保留（水印 2/3、身份 2/3、白底 3/3），本次套图的误报全部消除 |
| A60 | 低 | **体检文案自相矛盾**：实测 `main_selling_point` 边缘 249.72 → 四舍五入成 250 → 打出「背景不是纯白：边缘平均亮度 **250 < 250**」 | 数值保留一位小数（`{mean:.1f}`），并加回归测试断言"文案里的数值与实测值一致、不出现 250 < 250" |

**真机确认的两项待办（不是 bug，是质量上限）**：

1. **背景未达严格纯白**：最好的槽位是 251.8 / 249.7（阈值 250，Amazon 类目要求 RGB 255,255,255）。
   候选：生成后本地白点校正（原计划 L5-21，当时按"治标"搁置）或在提示词里进一步强调。
2. **包装图形元素简化**：参考图中央的肝臟解剖圖与 6 条英文引线在生成图里被简化/省略
   （文字本身已逐字保住）。候选：把"必须保持的视觉元素"从分析结果（`visible_text` / 新加
   `packaging_graphics`）显式写进槽位提示词，或在参考图之外再传一张包装正面特写。

**测试增量（第十六批）**：后端 **+6**（1190 → **1196**，均为 `test_harness/test_image_quality.py`
新增的误报回归：场景图深角落不判水印、特写图压角不判水印、非白底槽位不报白底、
非浅色生成图不判身份丢失、文案与实测值一致、汇总跳过非白底槽位）。
全量 **1196 passed / 10 deselected**（0 失败）。真实花费：探针 **$0.04** + 套图会话 **$0.4078**
（含被误报触发的一轮重生成 $0.2）≈ **¥3.2**。

### A61-A63 第十七批（2026-09-16：用户追问"为什么全是白底商品图"——补上真正的套图角色）

> 触发：用户看完真实会话的 5 张图后指出——"为什么生成的全是白背景＋商品的图，我记得我要求
> 一次性生成的要一套可以实际使用的图片，例如『纯商品图+成分图+商品面向人群图片+商品效果列举图』
> 这种一套图片"。

**取证（读回真实会话的 `set_plan` 与本地体检）**：5 个槽位是
`main_white`（白底商品照）、`main_selling_point`（提示词原文："…画面左上方与上方保留大片纯净白色
留白区域，**供后期贴图使用**…"）、`main_scene`（桌面场景）、`main_detail`（白底微距）、
`main_spec`（"…右侧与下方留出整洁的纯白留白区域，**供后期贴规格参数标签使用**…"）。
体检背景亮度 251.8 / 249.7 / 200.6 / 228.0 / 251.6 —— **确实 3 张是白底商品照**。
两个根因**都在我这侧**：

1. **槽位词表太窄**：`config/platforms.yaml` 只定义了
   `main_white / main_selling_point / main_scene / main_detail / main_spec / main_cert`
   —— **没有"成分图/面向人群图/效果列举图"这些角色**，模型自然只能产出商品照的几种构图；
2. **把"模型不画字"推到了极端**：A48 之后我要求"需要文字的版面描述成干净留白，供后期贴图"，
   于是卖点图/规格图退化成"白底商品照 + 一块空白"，信息图失去全部意义。

| # | 严重度 | 问题 | 修复 |
|---|--------|------|------|
| A61 | **高** | **套图只有商品照、没有信息图**（用户要的不止商品照）：词表里没有成分/人群/功效/用法/对比等角色，且信息类版面被"留白交后期贴图"掏空 | **槽位词表扩成真实电商套图角色**（新增 `slot_labels` + **`slot_catalog`**）：纯摄影 `main_white/main_scene/main_detail/note_*`；信息图 `main_selling_point/main_benefits/main_ingredients/main_audience/main_spec/main_usage/main_cert/main_compare`。每个槽位在目录里声明 `kind`（photo/info）、`usage`（main 主图 / detail 详情图）、`copy`（文案来源字段）、`layout`（排版模板）。各平台槽位重排：淘宝/天猫/京东 = 5 主图（纯商品/卖点/功效/成分/人群）+ 详情图（规格/用法/资质）；拼多多 = 6 主图 + 4 详情图；**Amazon 主图只保留纯白无文字的 `main_white`**，卖点/成分类放次要图位。`normalize_set_plan` 改为**主图与详情图分别限额**（此前一刀切按主图上限截断，会把信息图一起砍掉） |
| A62 | **高** | **信息图的文字没有可信来源**：交给生图模型画，中文（尤其繁体+®）必然糊或写错，且会顺手编出包装上根本没有的成分/认证/功效 —— 本商品分析员原话：「未見成分表 → **嚴禁臆造**奶薊草、水飛薊、姜黃、膽鹼等成分」 | 新增 **`harness/slot_copy.py`**：文案**只从已确认事实取**（身份卡 + 分析结果的成分/卖点/人群 + 包装可见文字转录 + 用户填写）；**缺依据 → `blocked` + 可执行原因**（如"包装正面看不到成分表：请上传包装背面/成分表照片"），**绝不编造**；另加**广告法违禁词过滤**（极限词/疗效词，**简繁并列**——实测该商品分析结论是繁体，只写简体会漏掉） |
| A63 | **高** | **信息图的落地方式**：需要"字形 100% 准确、文案可审可改、不额外花钱"的产出 | 新增 **`harness/image_compose.py` 本地排版引擎**（Pillow + 系统中文字体，自动探测微软雅黑/思源黑体/PingFang）：8 套版式模板（卖点 `top_title_bullets` / 功效 `benefit_grid` / 成分 `ingredient_list` / 人群 `audience_panel` / 规格 `spec_table` / 用法 `step_list` / 资质 `cert_badge` / 对比 `compare_two_column`），自动截断不溢出、品牌色块、页脚放品牌/规格/认证。链路：**信息槽位 = 模型出无字底图 + 本地排版文字层**（`image_gen._compose_info_slot`），保留无字底图 URL 供重排；`blocked` 的槽位不产图、计入 `set_plan_coverage.blocked_slots` 并**在群聊/前端显示"缺素材·未生成"**（协调者明确告知"重新邀请生图员没有用，需要用户补素材"，避免又白跑一轮烧钱）。**本地实机渲染样例**（零成本，用该商品真实信息）：卖点图 6 条 / 功效图 6 条 / 人群图 6 条 / 规格表 3 行均成功，成分图按预期 **blocked** 并给出补素材指引 |

**测试增量（第十七批）**：后端 **+34**（1196 → **1230**）：`test_harness/test_info_slots.py` 27
（槽位 kind/usage/layout 契约、主图-详情图分别限额、文案事实边界与 blocked 原因、违禁词过滤、
8 套版式逐一出图、超长文本不溢出、blocked 不产图）+ `test_agents/test_image_gen_info_slots.py` 6
（纯摄影槽位不受影响、信息槽位合成后 `text_status=composed`、成分缺依据 → `blocked` 且不计入成套、
覆盖率标记）+ 既有模板契约测试更新。
全量 **1230 passed / 10 deselected**（0 失败），前端 **176 passed**，`npm run build` 干净。

**同批收尾（可视化与可复用，2026-09-16 追加）**：

- **Mock 模式也能看到信息图**：`MOCK_ANALYSIS` 补上**自带"演示"字样的占位文案**（卖点/成分/人群，
  不含任何真实商品事实，A52 的静态扫描仍然通过），`MOCK_PROMPTS.set_plan` 从 3 个纯摄影槽位扩到
  **5 个（2 摄影 + 3 信息图）**。实测 E2E S1：摄影槽位仍是 SVG 占位图，而**信息槽位产出的是真
  JPEG**（本地排版），套图覆盖率 5/5 完整、`blocked_slots=[]` —— 即新链路在零成本模式下也被端到端
  覆盖（此前 Mock 下这条路径根本没被走到）。
- **`scripts/render_info_samples.py`**（新增，零花费）：拿任意会话的真实商品信息**只重排文字层**，
  用于调字号/配色/版式而不必重新生图。实测（拼多多会话 + 真机白底图作底图）：
  `--all-slots` 渲染出卖点/功效/人群/规格/资质 5 张，**成分/用法/对比 3 张按预期拦下并给出补素材指引**；
  同时产出 `manifest.json`（每张的标题/条目/页脚/版式/字体 + blocked 原因）。
- **前端可核对图内文字**：会话页图片卡显示"图文已排版 + 版式名 + 图上实际画了什么字"，
  缺素材的槽位显示红色原因；套图编排卡把 blocked 槽位标成 ⛔ 并写明"已拦下未编造"；
  平台选择器显示"主图 N + 图文 M"与**套图角色**（信息图带"（图文）"标记）。前端 **180 passed**（+4）。

**顺带被测试抓出的两个真 bug（A64/A65）** —— Mock 套图从 3 张变 5 张后会话变长，
触发上下文压缩，于是两条潜伏缺陷暴露：

| # | 严重度 | 问题（实测证据） | 修复 |
|---|--------|------------------|------|
| A64 | 中 | **压缩摘要消息没有标准消息信封**：`ContextManager.compact()` 往历史里塞的是 `{"role","content"}`（且 content 是**字符串**），没有 `id/turn/timestamp/sender/action` → 前端拿不到 `msg.id` 当 React key、也读不到 content 里的字段（测试立刻报 `assert 'id' in msg` 失败） | 摘要消息改为与其它消息**同一套信封**（`id/turn/timestamp/role/sender/action`），`content` 改为对象 `{context_action, compacted_count, summary, message}` |
| A65 | 中 | **品类专项分析员把"低置信度告警"清掉了**：`_update_artifacts` 合并 `analysis` 时，对 `_output_issues/_low_confidence_warning` 采用"本轮没有就清掉"——本意是让分析员重跑能刷新陈旧告警，但**品类专项分析员只是补充维度**，它没有这个字段不代表告警失效 → 界面完全不提示"分析置信度偏低"（产物里也查不到该标记） | 只有 `agent_name == "商品分析员"` 时才允许清掉这两个元字段；品类专项分析员的合并一律保留既有告警（同 A52 身份卡的保护思路） |
| A66 | 中 | **用户手工补充的事实被忽略**：`build_slot_copy` 的优先级把"内置兜底"排在用户输入之前，于是用户明明填了"食用方法/对比对象"，系统仍判 `blocked`（夹具测试抓到：`main_usage` / `main_compare` 在提供 `user_copy` 后依然不出图）—— 而这两类信息**恰恰只能靠用户提供**（包装正面看不到） | 优先级改为 **分析结论 → 用户填写 → 内置兜底（身份卡/包装文字）→ 缺依据**；`scripts/render_info_samples.py` 加 `--user-copy` / `--user-copy-file`（JSON，Windows 命令行引号易被 shell 吃掉故支持文件）。实测：提供"每日 2 粒…"与"升级版对比"后，用法图 2 条、对比图 2 条正常渲染 |

**最终验证（第十七批收尾）**：后端 **1261 passed / 10 deselected**（0 失败）、前端 **182 passed**、
`npm run build` 干净、**E2E 冒烟 7/7 通过**（S1 断言已覆盖：身份卡 / 套图覆盖完整 5 槽位 /
每张带槽位 / 落盘名含槽位 / 本地体检 / 生图参数可复盘）。信息图样例（零成本渲染）：
`output/info-samples/` 8 类槽位渲染出 **7 张**（卖点/功效/人群/规格/资质/用法/对比），
**成分图按预期拦下**并给出"请上传包装背面/成分表照片"——即"缺素材就不编造"的边界在界面上可见。

**排版样式可设置（A67，同批追加）** —— 用户看完成图必然要调排版，所以把它变成配置而不是常量：

| # | 说明 |
|---|------|
| A67 | **信息图排版样式可设置**：`config/image.yaml → typography` 新增 `font_scale`(0.6–1.8 字号倍率) / `max_items`(1–8 每图条目上限) / `brand_color`·`accent_color`(`#RRGGBB`) / `show_footer`；`image_compose.render_info_image(..., typography=…)` 全面接入（字号、色块、条目上限、页脚），排版 meta 回传**生效参数与 `truncated` 截断说明**（哪段文字被截断/省略几条，用户据此缩短文案或调字号）；`POST /api/settings/image` 白名单加 `typography`；设置页「🖼️ 生图质量策略」新增四个控件（字号倍率 / 每图最多条目 / 主色 / 辅色 + 页脚开关）；`scripts/render_info_samples.py` 支持 `--typography-file` 临时试样式（不动配置）。**顺带修一个部分保存语义 bug**：`save_image_settings` 对 `quality` 与 `typography` 都是"把 `clean_*` 的结果整体 update"，而 `clean_*` 会给缺失键填默认值 → **只改一项会把其余项重置成默认**（测试抓到：先存 `font_scale=1.5` 再存 `max_items=3`，字号被打回 1.0）→ 改为"先合并已存值再规范化" |

**测试增量（A67）**：后端 **+24**（1237 → **1261**）：`test_core/test_image_options.py` 新增
`TestTypography` 11 例（默认值/读回/部分保存不丢/10 种非法值拒绝/非对象拒绝/models.yaml 与
override 优先级）+ `test_harness/test_info_slots.py` 新增 `TestTypographySettings` 7 例
（meta 回传生效参数、条目上限与省略说明、主色真的画进标题条像素、页脚开关、字号倍率、
非法值回落、截断上报）。前端 **+2**（182）。

**用户补充事实的入口（A68，同批追加）** —— 补上"缺素材 → 能恢复"的最后一环：

| # | 说明 |
|---|------|
| A68 | **真实会话此前无法补事实**：信息图文案只取已确认事实，缺依据一律 `blocked`（这是对的），但用户手上往往就有信息（知道食用方法、想跟旧包装对比）—— 而 `user_copy` **只接在离线渲染脚本上**，真实会话没有任何入口，被拦下的槽位只能靠人工改代码重跑。新增 `POST /api/sessions/{id}/facts`：白名单 9 类来源（ingredients/features/selling_points/target_audience/scene_suggestions/usage/compare/spec/certifications），校验未知键/条目过长(>60 字)/条数超限(>8)/空值一律 400；写入 `task.product_facts` 并**落盘 checkpoint**、群聊播报；`image_gen._compose_info_slot(..., task)` 把它作为 `user_copy` 取用。前端会话页在**缺素材的槽位下方就地给出补充表单**（按槽位映射到对应事实类型），提交后自动经插话通道请协调者重新出图 —— 即"填了才画、不填不编"。**顺带修一个真 bug**：`_session_manager.update(...)` 是**协程**，漏 `await` 时事实只在内存里，重启即丢（测试的 RuntimeWarning + 新增的 checkpoint 持久化断言抓到） |

**测试增量（A68）**：后端 **+14**（1261 → **1275**）：`tests/test_api/test_session_facts.py`
（保存/读回/单字符串/合并既有/群聊播报/**checkpoint 持久化**/未知会话 404/7 种非法载荷 400/
**端到端：补事实后原本 blocked 的槽位变成 composed**）。前端 **+2**（184：补充表单提交
→ `saveSessionFacts` + `interjectSession`、空表单不发请求）。OpenAPI 快照已同步重新生成。

**验收工具（A69，同批追加）**：`scripts/real_suite_run.py` —— 一条命令跑完整轮真实会话并出
交付物报告（`--images 正面.jpg 背面.jpg --platform pinduoduo`）。要点：
① **成本闸门** `--max-cost`（默认 $3，超限即中止会话，防无人值守烧钱）；
② **不自动重跑**（终态/人工审批即停，把决定权交回用户 —— 实测协调者自行重跑会让成本翻倍）；
③ 报告含逐槽位明细（尺寸/参考图数/白底/水印/身份相似度/图文是否排版或为何被拦）、
套图覆盖度、体检结论、审查的**原图比对结论**、合规、**实际花费**，落盘
`output/real-suite/<时间戳>/report.json`；④ `--dry-run` 先估算不花钱（实测：拼多多 10 槽位
≈ $0.40，重跑翻倍）。纯逻辑（成本闸门/报告汇总）有 7 条单测；`tests/test_scripts` 共 12 例。

**第十七批最终数字**：后端 **1280 passed / 10 deselected**（0 失败）、前端 **184 passed**、
`npm run build` 干净。信息图样例（零成本）：`output/info-samples/` **8 类渲染出 6 张**
（卖点/功效/人群/规格/资质/用法）——其中**「使用方法图」是靠"补充事实"恢复出来的**（我用新端点
写了一条用法事实，渲染器从会话的 `task.product_facts` 读到并出图），剩 2 张按预期拦下
（成分缺背面照、对比缺对比素材），即"缺素材就不编造"在真实数据上可见。

**下一步（待用户上传包装背面/成分表照片）**：真实套图验收（拼多多 6 主图 + 4 详情图 ≈ ¥2）。

---

### A70-A78 第十八批（2026-09-18：用户实测反馈 —— "审美不合格"与"没有逐张约束"，外加"到底有没有白底硬规则"）

**现场取证**：用户上传 4 张包装图（正/背/侧/成分表）跑了一轮拼多多套图（会话 `ee9a3b80e19b4010`，
**6 张**、8 轮、$0.2519，用户最后点了 **reject**）。从 checkpoint、审计与产物里逐条还原：

| # | 现象（用户原话） | 取证 |
|---|---|---|
| ① | "生成的图片质量太差了，缺少了该有的商品图审美" | 提示词是 550–610 字的堆叠长句（近半是否定词）；**把包装版式逐字写进画面描述**（"左上深藍色品牌區塊、金色金屬燙印粗體字…肝臟解剖線稿圖示"）→ 模型按文字重画小字：审查员独立认定 `Sickle Ligament→Sadle Ligement`、`Liver Edge→Liver Edde`、手写体 `Schneiski` 变乱码；本地身份相似度 **0.22–0.33**（阈值 0.45，`main_white` 判 `identity_lost`）；6 张画面高度同质（都是"纯白 + 盒子 + 胶囊 + 迷迭香"）；卖点图**画面里没有商品实物** |
| ② | "他未有明确约束每一张该有的提示词（第一张提示词：…第二张提示词：…）" | 群聊里提示词生成员的消息是**裸 JSON**（前端 `msgPreview` 只截 120 字）；落盘 `prompt_text` 被 `prompt[:200]` 截断；前端只显示 `prompt_name`；更根本的是**平台规范块只渲染主图槽位** → 拼多多的 4 个详情槽位从未进入模型视野，**一张都没出** |
| ③ | 同批发现的硬缺陷 | 信息图**掉分辨率**（2048 底图被强制缩到 1200）；排版把"每粒 0.391g"**截断成"每粒 0.3"**（事实被截成错信息）；10 张 ≈505s > 生图员 420s 预算 → **必然整轮超时**，且 `artifacts["images"]` 是整键替换会丢已出的图；审查员 `image_parts(limit=3)` → **只审了 3 张** |

| ID | 优先级 | 问题 | 修复 |
|----|--------|------|------|
| A70 | **高** | **"每一张该有什么"没有契约**：槽位只有 `kind/usage/layout`，画面要求全靠模型自由发挥；平台规范块还不含详情槽位 | `config/platforms.yaml → slot_catalog` 每个槽位补齐 **`intent`（这张图要让买家看懂什么）/ `design`（版式·背景·光影·材质的设计要点）/ `must`（硬性）/ `forbid`（本槽位禁止）/ `keep_clear`（信息图留白区）**（15 个槽位）；`core/platforms.py` 增 `slot_spec/slot_intent/slot_design/slot_must/slot_forbid/slot_keep_clear/slot_contract_block/platform_slot_table/slot_table_block`；`platform_style_block()` 渲染**编号槽位表**（`第N张｜slot_id（角色）｜主图/详情｜画幅｜产出方式` + 逐张契约）**并补上 `detail_slots`**；`set_plan.normalize_slot` 补齐 `number`（**按平台规范顺序编号**，不按模型输出顺序）与全部契约字段（**契约来自配置，模型改不动**）；`normalize_set_plan` 输出顺序 = 平台规范顺序，缺槽位在 `notes` 里点名 |
| A71 | **高** | **提示词复述包装版式 → 模型重画小字**（实测乱码的直接成因） | `product_identity.identity_terms()`（品牌/品名/规格/认证 + 分词变体，剔"德国/原裝"等停用词与 <3 字符 ASCII 片段）；`image_prompt.strip_identity_terms()` 从画面描述里**移除身份词**并补身份句（`以参考图（图一）为唯一依据，逐字逐样保留包装图案与文字`），移除项记进 `prompt_notes`（**不静默**）；**品牌色值不受此限**（色值是包装上的事实，而且是"正规感"最便宜的来源） |
| A72 | **高** | **审美方向错了**：提示词写成"真实摄影说明书"，出来的是平淡写实图；拼多多风格块还写着"弱化高级感/不要大面积留白"（与信息图版式需求直接打架） | `image_prompt.py` 新增 `ART_DIRECTION_GUIDE`（给生成/审核看的完整设计标准）与进最终提示词的**精简**风格行 `PHOTO_STYLE_LINE/INFO_STYLE_LINE`；**六段式组装** `build_image_prompt()`（`第N张｜角色` + 【画面】【品牌色系】【必须】【留白】【禁止】【文字】【身份】），画面段上限 260 字（**契约段不受裁剪**），否定项合并成一段（不再各写一句）；`config/platforms.yaml` 重写拼多多主图风格为"高明度大主体…**不是靠高饱和堆料**"，并给 10 个平台补 **`detail_style`（信息图版式风格）** 与 **`art_style`（设计调性）**；顶层新增 **`art_direction`**（色板角色/允许元素/元素预算/设计禁忌/留白基准） |
| A73 | **中** | **信息图掉分辨率 + 文字被截断成错信息** | `compose_info_image` **不再按 1:1 缩到 1200**（有底图就保持底图分辨率，无底图用 `DEFAULT_INFO_CANVAS=2048`）；`_draw_text` 改**自适应**：整行 → 二分断点换行（≤2 行）→ 逐档缩字号（至 0.7）→ 最后才截断并记账（实测：6 条卖点与"每粒 0.391g"页脚**零截断**）；条目区加**文字底卡**（设计底图上也能读清）；`LAYOUT_CAPACITY` 按各模板行高算出真实容量（step_list 5 / cert_badge 4，其余 6），超出时记账说明 |
| A74 | **高** | **10 张必然超时 + 重跑丢图** | `BaseAgent.timeout_budget(session)` 钩子（默认 `timeout_ms`）；生图员覆写为 `max(420s, 张数 × 90s + 60s)`（上限 20 分钟）——实测 6 张 303s、10 张 ≈505s；`engine._merge_images()` 把 `artifacts["images"]` 由**整键替换**改为**按槽位合并**（键 = `slot_id` 优先、否则 `prompt_name`；命中原位替换、新键追加；整批无键回落整键替换）——"补跑/重出部分槽位"不再抹掉已出的图 |
| A75 | **中** | **只审了 3 张**：`image_parts(images, limit=3)`，审查员自己报告"main_ingredients、main_benefits 未送入审查" | 审查员**分批送审**（每批 ≤3、上限 4 批 = ≤12 张覆盖整套），逐批给顶层判定后由 `normalize_review_payload` 汇总，未审槽位记入产物与群聊；`config/prompts/reviewer.yaml` 增第 6 维度 **`realism`（AI 塑料感/过饱和/HDR 味/伪影）**、**信息图排版可读性**检查、按设计语言的评分锚点，并明确"`design_allowed` 平台背景不是纯白不算问题" |
| A76 | **高** | **逐张提示词对用户不可见** | `image_gen` 记录 `prompt_text` **全文**（≤4000 字，改前 200 字截断）+ `prompt_number/prompt_sections/aesthetic_score/revised_by_reviewer/prompt_notes`；提示词生成员与审核员载荷带 `message`（人类可读编号清单）；前端 `msgPreview` 增 `prompt_plan/prompt_lint/prompt_review` 分支并**结构化渲染**（`PromptPlanView`/`PromptReviewView`），会话页图片卡显示 `第N张`＋**提示词折叠区**（完整文本/契约项/改写痕迹），产出物新增「🧪 提示词审核」卡片 |
| A77 | **高** | **"符合基本要求但审美不合格"缺少零成本护栏** | 新模块 `harness/prompt_lint.py`（纯函数、**$0**、确定性）：规则 —— `missing_slot`(e)/`missing_intent`(w)/`identity_in_prompt`(e)/`packaging_prose`(w)/`fake_text_request`(e)/`missing_keep_clear`(e)/`missing_design_terms`(e，版式·背景·光影各≥1)/`missing_palette`(w)/`negation_pile`(w，>6 处)/`magic_words`(w)/`contradiction`(w)/`prompt_too_short`/`prompt_too_long`(w)/`forbidden_claim`(e，复用 `slot_copy.FORBIDDEN_CLAIMS`)/`white_required_bg`(e)/`duplicate_prompts`(w，3-gram Jaccard>0.6)；`prompt_digest()` 供幂等跳过；结论进 `artifacts.prompt_lint` 并在群聊播报 |
| A78 | **高** | **提示词没有"审美把关 + 优化"**（用户的初衷：让提示词接近大众商品图审美） | 新 Agent **「提示词审核优化员」**（`requires: [text]`，Mock 下显式标注演示数据）：按**设计七项**逐张打分（版式/色彩纪律/背景设计/光影精修/材质/呼吸留白/反廉价信号），**低于阈值（默认 85）直接给改写稿**；`harness/prompt_review.py` 负责规范化、阈值挑选与**安全落地**——改写**只能改《画面》段**（`must/avoid/keep_clear/palette` 来自配置，改不掉），且**逐槽位过体检**（会让该槽位新增硬伤的改写一律拒绝并记账）；引擎抽出 `_prompt_stage()`/`_review_prompts()`，**三处生成提示词的入口**（协调者邀请 / 审查 retry 直连 / 人工 retry 直连）统一接线，审查意见作为 `aesthetic_feedback` 回流；体检仍有硬伤 → 打回提示词生成员**至多 N 轮**（会话级计数，人工介入清零）；审核员报错/无 provider → 记 `skipped|error` **继续出图**（禁止静默当通过）；`chat.require_prompt_review/prompt_aesthetic_threshold/prompt_review_max_rounds/require_prompt_confirm` 四键可配（设置页「🛑 会话策略」），协调者产物状态新增"提示词审核：审美分 xx→xx，已改写 N 张（**不要重复邀请提示词生成员**）"；**顺带修** `resume_after_hitl(approve)` 在没有审查产物时伪造 `artifacts.review={"verdict":"pass"}` 的老 bug |

**同批的两次"我错了"更正（用户质疑成立）**

1. **白底并非全局硬规则** —— 用户："我其实是没看到有平台真有白底硬规则这个说法，很多商品图都不是白底的啊？"核实：跨平台**真正硬执行**的是"首图不得有文字/水印/边框/拼接"；白底按平台/类目/频道分档（Amazon 主图最硬，京东偏强，淘宝/拼多多现实中大量非白底）。落地：`config/platforms.yaml` 新增 **`background_policy: white_required | white_preferred | design_allowed`**（默认 Amazon=`white_required`、京东/1688=`white_preferred`、其余=`design_allowed`），**本地体检的白底判定改按该策略显式声明**（不再猜 `#FFFFFF` 字符串）——否则"要设计底、体检却报背景不白"会让协调者白跑一轮重生成（A59 踩过）。
2. **审美标准从"摄影工艺"改为"平面设计"** —— 用户："电商商品图需要类似于平面设计的风格的，背景不一定非要'真实'，而是要有一定的高级感，让人一看就觉得这个牌子看着挺不错挺正规的样子。"落地：判据改为设计七项 + **反模式清单**（写实随手拍感、长串否定、复述包装版式、`8K/超高解析度`魔法词、`高饱和+高级感`自相矛盾、槽位同质化、信息图不留白/无商品）；`config/prompts/prompt_reviewer.yaml` 内置**用本次真实废稿做的"坏→好"对照**。

**同批附加：品牌色系与槽位子集**

- **品牌色系**（A70 附带）：分析员新增 **`brand_palette {primary, secondary, background, evidence}`**（同一视觉调用，零额外成本）→ 进身份卡 → 注入提示词（`【品牌色系】…`）并作为本地排版的默认主/辅色（用户未自定义 `typography` 时生效）——"一眼看着正规"最便宜的一招。
- **槽位子集**（A74 附带）：`POST /api/sessions` 新增 `slots` 表单字段（`_clean_slot_subset`：白名单字符、≤12 个、非法 400）→ `task.slot_override` → 出图/体检/覆盖度全部只针对该子集；`scripts/real_suite_run.py` 新增 `--slots`（**最小付费冒烟**：2 张 ≈$0.08）与 `--variants draft,refined`（**A/B**：关闭/开启审美改写各跑一轮，≈$0.16，用于判定"改写 vs 还原度"的取舍），报告新增逐张提示词/审美分/改写痕迹/品牌色系/审查维度分与"已审·未审槽位"。
- **顺带修一个性能坑**：`core/platforms.py` 的访问器每次调用都重新解析 15KB 的 `platforms.yaml`（~27ms），而槽位契约把调用次数放大了几十倍 → 渲染一次平台规范块 **10.8s**、一次体检 6.5s。改为按 `(路径, mtime_ns, size)` 缓存解析结果（**配置改了立刻生效**）：`platform_style_block` **10.79s → 0.02s**、`finalize_prompts` 3.17s → 0.04s；后端全量测试从 427s 降到 **306s**。

**测试增量（A70-A78）**：后端 1280 → **1363 passed / 10 deselected**（0 失败）。新增
`tests/test_harness/test_prompt_lint.py`（规则逐条 + 干净 plan 零告警 + digest 稳定性）、
`tests/test_harness/test_prompt_review.py`（规范化/阈值挑选/**改写稿被体检拦下**/契约字段不被改写）、
`tests/test_agents/test_prompt_reviewer.py`（Mock 标注/无编排跳过/真实解析/报错透传/输入带契约与体检）、
`tests/test_chat/test_prompt_review_gate.py`（**三处接线**、低分改写落地、改写被拦保留原稿、digest 幂等不重复花钱、
硬伤打回恰一轮、开关关闭只记体检、审核员报错继续出图、`require_prompt_confirm` 暂停、审美反馈回流）、
`tests/test_api/test_session_slots.py`（子集落 task/空值走全套/非法 400/超量 400/只出子集）、
`tests/test_harness/test_image_compose_fit.py`（**零截断**：6 条长条目 + "每粒 0.391g" 页脚；保分辨率 2048；
按版式容量限条；品牌色与页脚开关；**白底预期按平台策略** —— `design_allowed` 不再要求纯白、
`white_required` 只对纯摄影槽位）、
`tests/test_chat/test_images_merge.py`（产物按槽位合并四情形 + 旧路径回落）、
`tests/test_harness/test_identity_palette.py`（身份词分词与移除、残渣清理、色值不算身份词、色板规范化）；
同步更新：`test_platforms.py`（契约字段/编号表/背景策略三档/拼多多设计导向措辞）、
`test_prompt_set_plan.py`（**平台顺序编号**+契约字段）、`test_image_gen_slots.py`（编号/全文提示词/旧路径）、
`test_info_slots.py`（容量）、`test_chat/*`（受影响断言）。
前端 184 → **196 passed**（`msgPreview` 三个新分支、`PromptPlanView`/`PromptReviewView`、
会话页 `第N张`＋提示词折叠、提示词审核卡片、设置页四个新控件与非法输入校验），`npm run build` 干净。

**第十八批最终数字**：后端 **1363 passed / 10 deselected**、前端 **196 passed**、`npm run build` 干净、
`scripts/real_suite_run.py --dry-run` 实测"2 槽位 × 2 轮 ≈ $0.16"。

**下一步（需用户批准付费）**：① 冒烟 A/B（`--images 正面.jpg 背面.jpg --slots main_white,main_selling_point --variants draft,refined`，≈$0.16）
并排给出 draft/refined 由用户判定"像不像正经品牌图"；② 达标后跑整套 10 张（≈$0.40）。

---

### A79-A96 第十九批（2026-09-18：用户指定「风格词库」+ 用户两次质疑）

> 触发：① **用户指定前端模块**——"在左边页面栏记忆库下面加一个『风格词库』，然后里面流程是，
> 进去会有个空白卡片中间有个＋号，然后点击会出现悬浮弹窗卡片，里面可以导入照片，对这个风格命名，
> 然后点击开始生成会有专门的 agent 帮我分析这组照片的风格。这只是我初步的预设，不是写死的，
> 希望你可以多提一些建议"；② 质疑成本显示——"不同的模型的花费是不同的，其次每个模型的花费
> 又会随着时间被各大模型商来回修改，这意味着除非每次模型商修改价格的时候都及时更新修改不然会
> 出现很大的误导，所以这个东西还是否有必要呢？"；③ 质疑后台 Agent——"为什么不跟中心决策者说明
> 风格分析员不参与会话，而是在风格分析员里写入，这不会导致中心决策者还会调用风格分析员，
> 只是会被拒绝而已吧？这样不会导致消耗额外的 token 吗？"
>
> 方法：先取证（读代码/配置，而不是猜），再落设计；**两次自查共 30 条遗漏**，逐条折进设计后才动手。

#### 一、成本质疑：成立，而且比用户说的更严重（A94-A96）

| 位置 | 当时的实现 | 为什么是误导 |
|---|---|---|
| `src/providers/openai.py:211` | `_estimate_cost(size)` = `{"1024x1024":0.04,…}.get(size, 0.04)` | 我们实际用 ark `doubao-seedream-5-0-260128` @**2048×2048**，没命中任何键 → **落到兜底 0.04**。这个数字是 DALL·E 3 时代的常量，与方舟实际计费无关（所有"≈$0.04/张"的说法都源于此） |
| `cost_tracker.py:108` | 未知模型按 `(1.0, 3.0)`/1M tokens | **凭空造金额**（还偏高一个数量级） |
| `cost_tracker.py:93` | `record_image` 未知模型回落 `(0.04, 0.04)` | 同上 |
| `providers/seedream.py:183` | `return 0.0  # 通常有免费额度` | 把**假设**当事实写进账 |
| 6 个 provider / 8 处前端 | 各写一份常量价 / `$x.toFixed(4)` | 一改价全失真，且**没有任何入口能让用户改** |

**修复（A94-A96）**：

- 新增 `harness/pricing.py`：**用量是事实**（路由/模型/张数/tokens/耗时，来自响应）**永远显示**；
  **金额是估算**，只在"有来源"时显示（供应商回报 → 用户填写/内置参考价/实测标定）；
  **未标定 → `amount=None`**，界面显示"未标定（N 张图）"，**绝不拿兜底常量编数字**；
- 删掉全部伪价格（`0.04`、`(1.0,3.0)`、`return 0.0`）；`None` 不再被 `or 0.0` 静默吞掉
  （`agents/base.py`、`chat/engine.py`、`ab_testing.py` 的 `sum()`、`audit_logger.round()`、
  `cli.py` 的 `%.4f`、`job_store` 的 `or 0.0` 逐点改；**DB 不加列**——该库只有
  `CREATE TABLE IF NOT EXISTS`，没有迁移机制，usage 走既有 `outputs_json`）；
- `config/pricing.yaml`（实例级，已 gitignore）+ 设置页「💰 计价」：列出**实际用过的模型**
  （`observed_models()` 从审计聚合），填单价；「标定」用"实际花费 ÷ 实际用量"反推单价；
  >90 天或偏差 >30% 给过期提示；
- 前端 `cost.js` 的 `formatCost()` 成为**全站唯一金额口径**（8 处 `$…toFixed()` 全部改走它）；
- `real_suite_run.py` 新增 **`--max-images`（张数硬闸门 = 事实量）**，`--max-cost` 改标注
  "按价格表估算、未标定模型不计"。

#### 二、后台 Agent：用户的 token 论证是对的，我原方案里的"双保险"是错的（A85）

用户指出：既然要"不参与会话"，为什么不直接告诉协调者，而要靠"被拒绝"？并追问会不会白耗 token。

**核实与结论**：

| 事实 | 证据 |
|---|---|
| Agent 名单是**每一次决策都重建并整段进系统提示**的 | `coordinator.py:67/101` → `_build_system_prompt()` → `config/prompts/coordinator.yaml` 的 `{agent_list}` |
| **我原方案里"在协调者提示词里写一句不要邀请它"是错的** | 那句话每轮都付 token，而且把名字**塞进**模型上下文，反而可能诱导它去提这个名字 |
| 移出名单 = **零额外 token** | 名单行数与加这个 Agent 之前相同（过滤掉它） |
| 名单外邀请**不调用任何 Provider** | `chat/engine.py:352-369` 的邀请路径；最坏只是浪费一轮**已付费**的决策，协调者读得到系统错误会自我纠正（`coordinator.py:493-496`） |

**落地**：`invitable: false` + **`registry.load_from_config()` 显式取出该字段**（`AgentMeta` 是
逐字段构造的，漏掉就静默失效 —— 与 A34/A41 同族的坑，新增
`tests/test_agents/test_agent_invitable.py` 专钉）+ `coordinator` 只列可邀请者（**过滤范围收窄**：
`/api/agents`、`/api/capabilities`、工作流模板校验仍看到全部）+ 引擎邀请走 `get_invitable()`。
**删除**了原方案里的协调者提示词那一句。

#### 三、两轮自查发现并修掉的真实缺陷（30 条中的关键项）

| # | 缺陷（实测证据） | 修复 |
|---|---|---|
| 1 | **Mock 路径不算 `style_refs`**：`prompt_gen.py:57-59` 在拼 user_msg 前 early-return → 前端"🎨 采用风格档案"整行没数据、e2e 断言落空 | 检索/渲染提成公共步骤，Mock 与真实都走 |
| 2 | **`apply_analysis` 用空串覆盖用户起的名字** → 词条随后因"缺少 name"被检索丢弃（"我明明起了名字却没用上"） | 缺席字段不覆盖；新增两个回归测试 |
| 3 | **停用的内置档案无法重新启用**：`load_library` 把停用项从 `entries` 移除，PATCH 找不到它 → 404 | 新增 `disabled` 清单与 `builtin_ids()`（含停用项）+ 界面标记未启用 |
| 4 | **预览请求非法槽位时静默放宽成"全部槽位"** | 如实回报"这些槽位不在该平台档案里，已忽略" |
| 5 | **相似度用 Jaccard 被长短差异稀释**（25 字包含 150 字长档案只有 0.13 → 永远不提示重复） | 改用**包含度** `text_overlap()`（交集/较短者） |
| 6 | 档案渲染**按槽位各写一遍** → 8 个信息图槽位命中同一条档案时 3000+ 字重复文本 | 改成 `### 档案定义`（每条一次）+ `### 逐张对应` |
| 7 | 冲突检测把**"无道具""文字由本地排版"**这类正当描述误报成与槽位契约冲突（18 条全是误报） | 命中处带否定词/委派措辞则不算冲突 |
| 8 | 用户词条会被内置通用档案挤掉（`max_entries` 默认只有 2） | 用户词条加成 20（亲手导入的风格代表他的选择） |
| 9 | 新端点会让 **OpenAPI 快照测试失败** | 重生成快照纳入完成定义 |
| 10 | 测试卫生：内置档案的启停状态是**持久化**的，测试改完不还原会污染同轮其它用例（实测风格库覆盖率用例集体失败，报错信息完全指不到原因） | 新增 autouse fixture 快照/还原 |

#### 四、交付物与证据

- `config/style_library.yaml`（内置 8 条档案，**加载 0 丢弃**）、`harness/style_library.py`、
  `harness/style_store.py`、`harness/pricing.py`、`agents/style_archivist.py`；
- `scripts/style_preview.py`（零成本自查：**10 个平台 × 全部槽位 100% 命中、与槽位契约 0 冲突**）、
  `scripts/style_anchor.py`（照片 → 判词；`--print` 零成本模板 / `--append` 先备份）；
- `/api/style-library*` 7 端点 + 前端「🎨 风格词库」页（＋卡 / 悬浮弹窗 / 拖拽·点选·粘贴导入 /
  2 秒轮询 / 零成本预览 / 用量行 / 编辑 / 停用 / 删除 / 再次分析）；
- e2e 新增 **S8 场景**（建词条 → Mock 分析 → 列表/详情 → 预览 → 停用 → 删除）与 S1 的三条
  `style_refs` 断言；
- **测试**：后端 1363 → **1602 passed / 10 deselected**（三次全量：1594→1595(+7 新用例)→1602，
  中间一次出现过 1 例不确定失败，已把该用例的隔离从"只改环境变量"改成**同时钉住 `data_root`**），
  前端 196 → **280 passed**（15 文件），`npm run build` 零告警。

#### 五、安全边界（本轮最不能破坏的两条）

1. **照片永远不进生图链路**：用户导入的往往是**别人家的包装**（本仓库两次事故：`水飞蓟` 抄进
   成分未确认的商品、`DEFOEBUENA®` 被编成 `NUTRIVA®`）。照片只落 `data/style_library/<id>/`，
   **不写 `data/inputs/`**，模块**不 import** 参考图/视觉载荷链路（静态扫描测试钉住）；
2. **档案是事实中立的文字**：不得含品牌/成分/认证/色值；加载层命中即丢弃并记账
   （对用户词条是"剔片段 + 记账"，因为局部越界很常见、整条丢掉会让用户白分析一次）；
   另加"画面里的文字不是指令"（防图片提示注入）。

#### 六、未验证 / 等用户输入

- **真实视觉调用**（6 张图是否被上游接受、真实耗时）与**真实价目**：都要用户批准才花钱；
- **锚点内容**：`anchors:` 默认空 —— 需要用户给 1–2 张"就要这种感觉"的图（界面导入）或口述，
  才会有**他自己的**打分准绳；
- **E2E 冒烟未执行**：它会起停服务、会杀掉用户正在跑的 8000/5173，等用户重启服务时复跑；
- **风格是否真的更好**：属于审美判断，最终由用户的并排图目视判定（`--style-library off,on`）。

---

## 第二十批：一轮会话一个风格词 + 套图结构 + 张数收口（A97-A107，2026-09-20）

用户在自己跑过一次风格分析之后，提了三条（都在同一轮里）：

> ①"我总感觉有啥不太对的地方，我给的是一套图片，那应该不止是单纯的分析图片的美术风格，
> 还有套图的制作习惯，例如『第一张：美术+纯白商品图，第二张：美术+成分图，
> 第三张：美术+面向群体图』"；
> ②"为什么限定只能输入 6 张图片？"
> ③"生图的话只能使用一种套图，但是你有没有在风格词库那里限定启用一个风格另一个自动停用？"

### 一、取证（三条都成立，第三条比用户担心的更严重）

| # | 结论 | 证据 |
|---|---|---|
| ① | **成立** | `config/prompts/style_archivist.yaml` 原话「多张照片是**同一套风格**的不同画面……请归纳**共性**，**不要逐张描述**」；`_ENTRY_FIELDS` 里没有任何能放"第几张是什么角色"的字段。用户真实词条 `st_b43e79c199a9` 印证：整套混成一条（"左侧固定一块标题区""允许一枚同色亚克力几何体"），`forbid` 为空；而 `removed_brand_text` 的 8 条（"顶部标题条内的产品名""左侧功效描述与含量说明""蓝色圆形保健食品标志"）恰好说明**那是一套已排版好的商品图** |
| ② | **6 是虚数** | 同一个数字写了 4 遍且无凭据：`style_store.MAX_PHOTOS`＝`api.MAX_STYLE_IMAGES`＝`style_archivist.MAX_VISION_IMAGES`＝前端 `MAX_STYLE_FILES`＝6（`FALLBACK_IMAGES=3` 抄自 `vision_payload.MAX_IMAGES`）。用户实测：6 张 = 1 次调用 **$0.000932**、落盘 617KB —— 体积与花费都不是瓶颈 |
| ③ | **成立且更严重** | 用用户真实词条跑 `select_by_slot(拼多多 + 保健品)`：**10 个槽位张张 2 条** ——「同色清新」+ 内置（`clean_hero_white`/`info_bullet_sheet`/`herbal_natural`…），且**硬矛盾**（"近白到浅粉渐层 + 亚克力几何体 + 玻璃球" vs "纯白无缝、主体 85–92%、无道具无装饰无文字"；信息图位上内置写"除留白区外不得出现图案"）。而 `describe_selection()` 只取 `picked[0]`，**群聊播报把第二条吞掉了**。此外三条检索入口（生成员/审核优化员/引擎体检）各自独立检索 → 中途在词库切换启用项，**同一套图会前几张用 A、后几张用 B** |

### 二、A97 一轮会话一个风格词（本轮第一优先级）

- **会话锁**：`session_style_lock(session)` 优先级 = `task.style_entry_id`（用户点「换风格」）
  → `artifacts.prompts.style_refs.locked_entry_id`（首次检索落下）→ 词库里唯一启用的用户词条
  → 内置原型。三个检索点（`prompt_gen._select_styles` / `prompt_reviewer._select_styles` /
  `engine._review_prompts`）**全部带锁**，否则审核会按另一条风格打分；
- **radio**：`style_store.set_entry_enabled()` 启用一条 → **同租户其他用户词条自动停用**，
  响应带 `auto_disabled` 名单；分析成功时才切换（`apply_analysis`），**失败则旧的照常生效**
  （不制造"没有风格"的空档）；界面把名单显示出来（卡片 + 列表提示）；
- **严格模式**：`style_library_max` 默认 **2 → 1**（语义改为「**整套最多用几种风格**」）。
  用户风格覆盖的槽位（有 `shot_roles` 按它判，没有则视为通用）→ **只注入它**；覆盖不到 →
  严格模式**返回空**（该张只按平台槽位契约写），补位模式（≥2）才允许内置原型补 1 条；
  锁定的词条不可用 → **回落内置并如实说明**（不静默换一条用户风格）；
- **播报如实**：`describe_selection()` 改为"本次会话风格「X」（已固定）；覆盖 6/10 张；
  仅平台槽位契约：第7张、第8张；内置兜底：…；锚点：…"；
- **换风格**：`POST /api/sessions/{id}/style`（写 `task.style_entry_id` + 清旧锁 + 群聊系统消息），
  会话页 `PromptAuditCard` 显示"本轮已固定"+ 按钮；
- **合规口子**：Amazon 这类 `white_required` 平台，"浅粉渐层"风格与首图"必须纯白"冲突 ——
  槽位契约仍然优先（代码注入，模型与审核员都改不掉），冲突在预览与 `--check` 里明确警告；
  弹窗补 `kinds`/`requires_policy`/`not_slots` 三个控件（**此前完全没有入口**）。

### 三、A98 套图结构（"我给的是一套图片"）

- 新增 `shot_flow`（整套叙事顺序）+ `shot_roles[]`（`number`/`slot`/`treatment`）；
  **角色词不由模型写**：模型只从 `main_white=纯商品图；…` 清单里挑 `slot`，界面显示的
  "纯白商品图/成分配方图"由 `slot_label()` 派生（自由写角色名会命中事实词表「成分」而被清洗掉）；
- **序号与照片严格对齐**：`normalize_shot_roles(..., image_count=)` 保留合法且未占用的序号 ——
  否则"第2张没识别出来"会把第3张挤成第2张，**提示词就按错的照片写画面**；
  界面上是**逐张行**（缩略图 + 角色下拉 + 做法输入 + 移除），可核对可修正；
- **提示词与事实中立**：角色差异只写画面结构；「成分配方图」这类**槽位角色名先豁免**
  （它是系统自己的受控词汇，不是"照片上的事实"）；
- 渲染新增 `### 参考套图的编排习惯`（参考第N张 = 什么角色 → 叙事顺序）+ 逐张"该张做法"/
  "仅按槽位契约写"；整段 ≤900 字且**只有带 `shot_roles` 的档案才产生**（旧档案逐字节不变）；
- 覆盖差 `sequence_coverage()`（`matched`/`missing_in_ref`/`extra_in_ref`）只进预览与
  `style_refs` 快照，**不进提示词**（不为不需要的信息付 token）。

### 四、A99-A105 张数、预算与照片增删

- **上限单一来源**：`MAX_PHOTOS=20`、`VISION_BATCH_SIZE=12` 定义在 `style_store`，
  接口与档案员 import，前端由 `stats.limits` 下发（**不再各写一份**）；
- **分批**：>12 张时第 1 批出全量字段、后续批只补 `shot_roles`（提示写明"这是整套的第 13–20 张"）；
  `usage.batches/calls/images` 按批聚合，**金额只在全部调用都回报时才求和**（否则 `None` + 说明）；
- **超时与收割**：`timeout_budget() = max(180s, 批数×90s+60s)`；
  `analyzing_timeout_s(张数) = 600 + (批数-1)×240` —— 否则 20 张会被误判"分析中断"，
  界面先显示失败、随后又翻成 ready（用户看到的是一次灵异事件）；
- **体积预算**：单次请求 ≤8MB，超了先降采样（长边 1280 / q78），仍超就送前缀并**如实记账**；
- **追加/移除照片**：追加只 append（序号不变 → 角色仍对得上，新照片显示"（未识别）"）；
  移除会重排 `photo_N.jpg` → **清空 `shot_roles` 并回报 `cleared_roles`**；
- **逐图打标**：`vision_payload.image_parts(labelled=True)` 在每张图前插"第N张"
  （否则多图任务里"第几张是什么角色"只能靠位置猜）。

### 五、顺带修掉的旧账（都有实测数字）

1. **界面在撒谎**：前端写"每张不超过 20MB"，而 `ImagePreprocessor.max_bytes` 默认 **10MB**
   （调用处未传）→ >10MB 必然 400。现在真实值经 `limits` 下发；
2. **`entry_conflicts` 的 36 处误报**：信息图槽位禁止项里的「文字/数字」把
   "主体与文字之间留气口""带内文字上下留 30%"全报成"与契约不一致"，把真问题埋掉 →
   这类片段不再参与档案冲突检测（改由 `prompt_lint` 在**提示词层面**精确拦截），
   同时逐张做法**只与它自己那个槽位**比对（跨槽位比对会误报）；
3. **`style_plain_text` 跨槽位比对**：`style_copy_overlap(prompt, entries, slot_id)` 只比该槽位的
   逐张做法，避免"X 槽抄了 Y 槽的做法"被报成命中；
4. **`--check` 会被用户词条带崩**：现在区分内置（配置错误 → 退出码 1）与用户词条
   （仅警告）—— 用户词条与槽位契约不一致是**正常现象**（如"浅粉渐层 vs 首图必须纯白"），
   注入时契约优先。

### 六、测试与验证（本轮）

- **后端**：`pytest -q` 全量 **1645 passed / 10 deselected / 0 failed**（**+43** 条新用例：会话锁/radio/严格模式/套图结构归一化与渲染/覆盖差/分批与合并/金额聚合/回落减半/体积预算/
  超时预算/逐图打标/照片增删/接口新端点/提示词骨架不含具体顺序）；
- **前端**：`npx vitest run` **294 passed**（**+14**：逐张角色行与保存 patch、追加/移除照片、
  套图结构 chip、"已自动停用"名单、上限来自接口、适用范围新控件、会话锁与覆盖差、`slotOptions/roleRows/rowsToRoles`）、`npm run build` 干净；
- **零成本自查**：`python scripts/style_preview.py --check` → 10 个平台**每张注入 [1] 条**、
  内置冲突 0、用户词条冲突 0（含本轮修的误报）。

### 七、未验证 / 等用户

- **真实视觉调用**：分批（>12 张）与逐图打标只在假 Provider 下验证过，**真实上游是否接受
  12 张/次、真实耗时**要用户批准才花钱；
- **用户那条词条要拿到新结构**：需点一次「再分析」（1–2 次调用；同口径上次实测
  **$0.000932 / 6 张**），若只导入了一部分照片可先「追加照片」；
- **E2E 冒烟未执行**：会起停服务、会杀掉用户正在跑的 8000/5173；
- **是否按参考套图重排本平台套图**：属产品决策，见 `docs/progress.md §3.3` 第 8 条。

---

## B. 待修清单（已复核，按优先级）

> 编号沿用原清单：B0 第 1-4 项、B1 第 5-9 项、B2 第 17-18 项、B3 第 24-26 项及 27 的图片部分已修复（见 A9-A23）；A24 为用户实测反馈新增。下方保留其余 15 项。

### B1 正确性 / 资源治理

10. **模板无终态节点 → job 永久 running**（中）— `validate_template` 不校验可达 end，`run()` 退出主循环却不写终态、无终态事件、`_runtimes` 残留（实测）。*修法*：`current is None` 且非终态 → FAILED；导入时校验终态可达。

11. **`retry_failed` 不回退 failed 计数**（中）— 与 `reconcile_batch_counts`（只增不减）组合 → done+failed 超 total 且永不回落（实测 4→5）。*修法*：reset 时递减或按 items 权威重算。

12. **A/B 评审拿不到变体输出**（中）— 评审只传 variant_id/label，reviewer 仍从 `session.artifacts` 取图 → 三变体同一批产物评分，winner 无意义；`_review_variant` 还把异常全吞。*修法*：把 `vr.output` 注入评审。

13. **`/api/sessions/{id}/ab-test` 畸形 body → 500 + 纯文本**（中）— 违反项目自身 `{"detail": …}` 契约（`review_count:"abc"`、变体缺字段；`test_error_contract.py` 未覆盖）。*修法*：字段白名单 + try/except → 400。

14. **`providers.yaml` 语法错误 → `GET /api/settings` 500**（中）— `load_yaml` 不捕 YAMLError；同时 Provider 构造异常被 `get_llm` 吞成 available=False（静默降级）。文档明确支持手改该文件。*修法*：兜 YAMLError 返回 {}，设置页回显"配置损坏"。

15. **图片预处理阻塞事件循环**（中）— 同步 PIL 在 async 端点直调：6000×4000 实测单图 326ms、事件循环卡 316ms，10 图 ≈3.9s。*修法*：三处 `asyncio.to_thread`（实测卡顿降至 13ms）。

16. **Docker 部署下配置功能不可用**（中）— `COPY config/` 使 `/app/config` 属 root，进程 `USER harness` 只 chown 了 `data/`、`output/` → 密钥/端点/模型/Agent 参数保存全部 PermissionError→500；`.dockerignore` 未排除 `secrets.yaml`/`providers.yaml`/`tenant_keys.yaml`（会被烤进镜像层）。*修法*：`chown -R harness:harness config/` + 排除密钥文件（本机无 docker，代码取证）。

### B2 工程化 / 开发体验

19. **文档里的 `pytest` 命令本机不可用**（中）— `pytest tests/...` 报 `No module named 'src'`（rootdir 未进 sys.path）；`pip install -e ".[dev]"` 因缺 `[build-system]` 报 `invalid command 'bdist_wheel'`。*修法*：`[tool.pytest.ini_options] pythonpath = ["."]`，补 build-system，文档统一 `python -m pytest`。
20. **覆盖率无门禁，基线文件甚至未入库**（中）— `docs/coverage_baseline.json` 未被 git 跟踪却被 test-plan/progress 当证据引用；CI 无 `--cov`。*修法*：入库 + `--cov-fail-under` + 按模块阈值。
21. **静态检查覆盖不全**（中）— ruff 只扫 `src tests`（`start.py`/`scripts/` 从未检查，实测 E501 多处）；前端无 lint/typecheck。
22. **解释器不一致**（中）— `requires-python = ">=3.12"`，本机实际 3.10.10 跑全部测试，CI 只跑 3.12，两侧互不验证。
23. **`.gitignore` GBK 混编 + 行尾混用**（低）— UTF-8 解码报错（第 20 行注释为 GBK 字节）；git 语义未坏。*修法*：UTF-8 重写统一行尾。

### B3 产品 / 体验（价值高，改动可控）

27. **生成结果无法下载/导出** — 图片只有 `<img>`，工作流节点输出是裸 JSON；批量报表无 CSV 导出。
28. **会话不能重跑/复制** — 换平台或重试要重传图片重填表单（工作流/批量早有 retry）。
29. **批量页不轮询** — 创建后进度条不会自己动（其他页 3-5s 轮询）。
30. **窄屏导航只剩 emoji**（无 title/aria-label）；错误反馈一半 alert 一半内联；401 文案是英文。

---

## C. 已核对、判定无需修（可关闭的方向）

- chat/providers 内**无密钥或提示词泄漏**（非 200 只回状态码，不含 body/header；无日志打印 prompt/key）；**无事件循环阻塞**（checkpoint 已 to_thread，provider 全 `async with httpx` + 超时）
- `expressions.py` 无 eval / 属性逃逸；`job_store.py` 唯一 f-string SQL 是常量插值、租户值走绑定参数；路径穿越与 SSRF（含 `::ffff:127.0.0.1`）判定充分
- Coordinator 决策失败回落 Mock 工作流是决策 5 的既有设计（有 warning 日志）；配合 A1 修复后，Agent 层失败不再被掩盖
- Broadcaster 无租户绑定、未配 Key 时 WS 不校验租户，与"未配置 Key = 开发模式"的文档设计一致
