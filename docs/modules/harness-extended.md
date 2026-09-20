# Harness 扩展 — 限流 / 成本 / 审计 / 上下文 / 记忆 / 输入输出管道 / 视觉图源

> 覆盖: `src/harness/rate_limiter.py`, `src/harness/cost_tracker.py`, `src/harness/audit_logger.py`, `src/harness/context_manager.py`, `src/harness/agent_memory.py`, `src/harness/input_pipeline.py`, `src/harness/output_pipeline.py`, `src/harness/vision_payload.py`
> 基础三件套（重试/超时/熔断）见 `harness.md`

## 功能

Harness 层的第二梯队模块，覆盖运行期治理与数据管道。

## 关键类

### RateLimiter (`rate_limiter.py`)

令牌桶限流，支持按 Provider/模型/token/租户多维计数。

```
acquire(provider, model, tokens, tenant_id)   # async，超限则等待
try_acquire(...) → bool                        # 非阻塞尝试
remaining(provider, ...) → int                 # 剩余 token（内部先 refill）
set_tenant_limit(tenant_id, rpm, tpm)          # 租户独立配额
configure(provider, model, rpm, ...)
```

- 桶结构 `_TokenBucket(rpm, tpm)`，双速率（请求数 + token 数）分别计量
- 全局单例经 `src/agents/base.py` 的 `get_rate_limiter()` 访问

### CostTracker (`cost_tracker.py`)

会话成本追踪与预算控制。

```
record(model, tokens_in, tokens_out, agent_name, provider)
record_image(model, count, agent_name)          # 图像成本单独计入 breakdown
total_cost() / remaining_budget() / is_over_budget() / is_warning()
breakdown() → {total, by_model, by_agent, images}
from_session(session) → CostTracker             # 类方法：取出/重建（A39）
```

- 预算与告警阈值来自 `config/default.yaml` 的 `cost` 段
- `BudgetExceeded` 异常在超预算时由调用方决定策略（当前引擎记录但不中断）
- **`from_session()` 是 A39 的关键**：checkpoint 是 JSON，存不了 `CostTracker` 实例
  （此前 `json.dump(default=str)` 把它写成 `"<… object at 0x…>"`，恢复后 `record()` 抛
  `AttributeError` 被吞 → 该会话**永久停止记账**）。现在实例不可用时按 `cost_so_far` 重建，
  `_track_cost` 与启动恢复路径都走它
- 图像计费：生图 Agent 把 Provider 的 `cost_usd`/`model_used` 回传后走 `record_image`
  （按张计费；方舟 Seedream 不在 `_PRICES` 里时用默认估算价 $0.04/张）

### 会话进度落盘（`chat/session.py` + `storage/checkpoint.py`，B3）

```
SessionManager.update(session_id, state, slim=False)   # slim=True → 轻量快照
save_checkpoint(session_id, state, slim=False)
```

- **每轮轻量快照**：`ChatEngine` 在每个 Agent 步骤后调 `update(..., slim=True)`，
  崩溃/强停最多丢当前这一步（此前只在人工暂停/终态/压缩前落盘，实测强停丢掉整段轨迹）
- **`slim` 剔掉 `task.{product_images,reference_images}`**（用户上传图的 base64）：
  实测某真实会话 checkpoint 3541KB，其中上传图占 3400KB → 轻量快照仅 **128KB（缩小 28 倍）**，
  避免每轮 3.5MB 的写放大
- 完整快照（含上传图）仍在**人工暂停 / 会话终态 / 上下文压缩前**写入，
  所以正常结束的会话在磁盘上是完整的
- 启动恢复会加载**全部** checkpoint（终态也加载，否则重启后历史会话从列表消失，A45）

### AuditLogger (`audit_logger.py`)

审计日志，按日期分文件写入 `data/audit/`（JSONL）。

```
log(session_id, event, agent_name, ...)         # async 追加一行
query(session_id, agent_name, date) → [entries]
stats(date) → dict                              # 汇总统计
```

- 同步文件 IO 经 `asyncio.to_thread` 转线程，避免阻塞事件循环
- 工作流事件溯源在 SQLite 双写审计（见 `workflow.md`）

### ContextManager (`context_manager.py`)

上下文窗口管理，防止 token 溢出。

```
check(messages, system_prompt) → ContextReport   # 包含估算 tokens + 建议动作
compact(messages, keep_first, keep_last) → (messages, summary)   # 压缩中间消息
new_window_summary(messages) → str               # 开新窗口摘要
```

- `WindowAction`：`NO_ACTION` / `COMPACT` / `NEW_WINDOW`
- system_prompt 计入阈值（审计修复 C25）；base64 图片载荷在估算时剔除

### AgentMemory (`agent_memory.py`)

跨会话记忆召回（JSONL 存储于 `data/memory/`，可用 `ECOMM_DATA_DIR` 重定向）。

```
remember(category, content, score, ...)          # 写入成功经验
recall(category, limit) → [entries]              # 按分数召回
recall_similar(category, query, limit)           # 关键词相似召回
stats() / clear(category)
```

- **并发安全**（第三轮审计 B1-6）：`_append_entry` 持模块级 `_MEMORY_WRITE_LOCK`
  （跨实例共享，同 `audit_logger` 的 `_WRITE_LOCK`）串行追加；此前无锁，实测
  200 并发 `remember()` 只落盘 183 条（丢 17）。序列化在锁外完成，锁内只有一次 write。

### 输入/输出管道

```
ImageValidator.validate(image_data) → ValidationResult   # 格式/大小/base64 校验（20MB 上限）
InputPipeline.process(image_data)                        # 验证 + 内容安全（占位）
SchemaValidator / FieldCompletenessChecker / BusinessRuleValidator
OutputPipeline.validate(agent_name, output) → OutputResult
```

- 输入管道在 `POST /api/sessions` 中被调用（上传图片先验证后预处理）
- 输出管道在 `chat/engine.py` 审查产出物时使用
- **不得抛异常**（第三轮审计 B1-5）：校验对象是不可信的 LLM 原始输出。所有数值比较
  先过 `is_number`（排除 `bool`），字符串判定用 `is_non_blank_str`；`OutputPipeline.validate`
  另有兜底 `try/except` → 保证任何畸形输入都只变成 `passed=False` + 可读 errors
  （此前 `{"overall_score": "85"}` 抛 `TypeError` → 整轮群聊失败）

### 视觉图源 (`vision_payload.py`)

把"生成图记录"转成视觉模型能吃的 content parts（A31）。

```
sniff_mime(data) → "image/jpeg" | "image/png" | "image/webp" | "image/gif" | "image/svg+xml"
image_parts(images, limit=3, *, output_root=None, downloader=None)
    → (content_parts, notes)
```

每张图按序尝试四种来源，任一命中即用：

1. `base64_data` —— **MIME 由魔数嗅探**（此前写死 `data:image/png`，而方舟返回的是 `.jpeg`）
2. `image_url` 是 `data:` URI —— 原样透传
3. `saved_path` —— 从输出根读**本地落盘文件**（引擎生图后已自动落盘，最稳）；
   路径必须落在输出根内（`saved_path` 来自产物字段，不校验就能读宿主机任意文件）
4. `image_url` 是 http(s) —— 下载一次转 base64（30s 超时、8MB 上限；`downloader` 可注入便于测试）

失败不抛异常，原因收进 `notes` 交给调用方（审查员据此提示"哪张拿不到"，且**无图时确定性报错、
不调用 LLM**）。合规审查员复用同一实现。

## 修改指南

- **调整限流额度** → `RateLimiter(default_rpm, default_tpm)` 或 `set_tenant_limit`
- **调整成本定价** → `cost_tracker.py` 的 `_PRICES` 表（与各 Provider `_estimate_cost` 保持口径一致）
- **接入真实内容安全 API** → `input_pipeline.py` 的 `ContentSafetyChecker.check`
- **新增输出校验规则** → `output_pipeline.py` 的 `BusinessRuleValidator`
- **新增图源形态**（如对象存储签名 URL）→ `vision_payload.image_parts` 的分支 + 注入 `downloader` 的单测

## 商品身份 / 套图 / 参考图 / 体检（A48-A54，2026-09-16）

本轮为"成图质量"新增的四个纯逻辑模块（都不调用 Provider，全部可离线单测）：

### `product_identity.py` —— 商品身份卡（全链路事实基准）

```
normalize_identity(analysis, *, source="vision") → {brand, product_name, spec,
    certifications, package_form, confidence, evidence, source, derived_from,
    visible_text, missing, status}
identity_summary(identity)   → 群聊播报行（✅ 已确认 / ⚠️ 未确认 + 缺什么）
identity_card_block(identity) → 注入下游简报的"事实基准"块（前置，权重最高）
is_confirmed(identity)
```

- **来源必须可溯源**：只有 `source ∈ {vision, user_confirmed}` 且品牌/品名齐全（vision 还要
  `confidence ≥ 0.5`）才算 `confirmed`；`mock` / `none` **永远** `uncertain`。
  这条直接回应"会不会把我这款商品套成别的牌子"——`MOCK_ANALYSIS` 里那套写死的
  "保健品/水飞蓟/蓝帽"正是曾经会冒充事实的污染源。
- **未确认就禁止编造**：`uncertain` 时下游提示词禁止出现任何品牌/品名/成分/认证文字，
  包装文字区域一律"干净虚化"交设计师后期贴图。

### `set_plan.py` —— 套图编排（交付物是"一整套"，不是"一张图的候选"）

```
normalize_set_plan(prompts, *, platform, slot_override=None) → {platform, slots[], notes[]} | None
set_plan_coverage(plan, images) → {expected, produced, done_slots, missing_slots, complete}
set_plan_summary(plan)
```

- 规范化：去重 slot_id、缺提示词的槽位跳过并记 note、按平台 `max_images` **截断**（生成多了也传不上去）、
  模型没给 slot_id 时按平台槽位顺序补位（保证落盘文件名可读）。
- 没有 `set_plan` 时返回 `None` → 生图员走旧的"同一提示词 × variants"路径（向后兼容，
  且提示词生成员会在产物里写 `set_plan_note` 说明"本次交付物不是套图"）。

### `reference_images.py` —— "文+图"里的"图"

```
to_data_uri(source) → (data URI, note)          # 裸 base64 / data URI 都能吃
collect_references(sources, limit=4) → (uris, notes)
reference_sources(session) → [base64, ...]      # 内存 → 磁盘 inputs/ 回退
```

- 嗅探 MIME（实测上传是 PNG，此前写死 `image/jpeg`）；超 4MB 自动降采样为 JPEG(q88)
  （实测 2.6MB PNG → data URI ≈3.5MB，先压再传）；单张失败只记说明，不抛异常。
- **磁盘回退**：轻量快照会剔除 `task.product_images`（base64 太占空间），崩溃恢复/重启后
  内存里没有上传图 —— 不回退的话 i2i 会**静默退化成纯文生图**（又回到编造包装文字的老路）。

### `image_quality.py` —— 本地体检（零成本、确定性、可回归）

```
inspect_image(data, *, thresholds) → {edge_mean, is_white_bg, watermark_zone_delta,
    watermark_suspected, subject_ratio, sharpness, issues[]}
compare_to_reference(reference, generated, thresholds) → {identity_similarity,
    global_similarity, background_shift, is_near_copy, identity_lost, issues[]}
inspect_images(images, *, thresholds, reference) → (reports, notes)   # 带 index 便于回写
summarize(reports) → {count, white_bg_ok, watermark_free, identity_lost[], near_copy[], issues[]}
```

| 指标 | 判据 | 实测锚点（本次真实产物） |
|------|------|--------------------------|
| `edge_mean` | 白底图应 ≥ `edge_whiteness`(250) | 三张图 240 / 238 / 235 → 均不合格 |
| `watermark_zone_delta` | 右下 70–100%×88–100% 与边缘亮度差 | −30.7 / −2.1 / −27.7 → 两张疑似水印 |
| `identity_similarity` | 主体区域颜色分布+结构相关 | 0.29 / 0.25 / 0.38 → 全部低于 0.45 |
| `is_near_copy` | 整图高度相似**且**背景未换 | False（本次是"跑偏"不是"复制"） |

即**本次事故不花钱就能在本地提前发现**；引擎在出图后写 `artifacts.quality_report` 并播报问题。
既防"退化成纯文生图"（身份丢失）也防"退化成纯图生图"（复制原图没重绘）。

### `image_prompt.py` —— 生图提示词组装（生图员与"试生成"共用一套规则）

用户 2026-09-18 两条反馈重塑了这个模块（A71-A72）：

> ①"生成的图片质量太差了，缺少了该有的商品图审美"
> ②"电商商品图需要类似于平面设计的风格的，背景不一定非要'真实'，而是要有一定的高级感，
> 让人一看就觉得这个牌子看着挺不错挺正规的样子。"

**方向**：不再追求纪实写真感；画面是"设计出来的"（背景可以是纯白 / 品牌浅色底 / 双色分割 /
径向渐层 / 微质感底），写实只用于**场景槽位**。

**六段式组装** `build_image_prompt(...) -> (提示词, 说明)`：

```
第3张｜成分配方图（main_ingredients）｜详情图｜1:1｜信息图·文字由系统本地排版
【画面】…（模型/审核优化员写的画面描述；上限 260 字）
【品牌色系】主色 #1B3F94 ｜ 辅色 #7CBF4A ｜ 背景 #EEF2F7（取自包装）
【必须】…（槽位契约 must + 精简风格行 PHOTO_STYLE_LINE / INFO_STYLE_LINE）
【留白】…（信息图留白区，供本地排版文字层）
【禁止】…（槽位 forbid + FORBIDDEN_LOOKS + NEGATIVE_FOLD，**合并成一段不堆叠**）
【文字】…（文字策略：信息图只能给无字底图；无参考图时强制虚化）
【身份】商品以参考图（图一）为唯一依据，逐字逐样保留包装图案与文字…
```

- **契约段不受长度裁剪**：超长只压《画面》段（`MAX_SCENE_CHARS`）并记账；
- **身份词移除** `strip_identity_terms(prompt, identity_terms(identity))`：品牌/品名/规格/认证
  的文字从画面描述里移除（实测写进提示词会诱导模型重画包装小字 → `Sadle Ligement` 乱码），
  移除项记进 `prompt_notes`（**不静默**）；**色值不在移除范围**；
- `ART_DIRECTION_GUIDE` / `INFO_BASELINE_GUIDE` 是**给提示词生成员与审核优化员看的长标准**，
  不进最终提示词（上一轮实测 586 字的"约束堆叠"会让画面呆板拘谨）。

`compose_prompt(...)` 保留为兼容入口（设置页"试生成一张"、`scripts/quality_probe.py` 在用）。

### `prompt_lint.py` + `prompt_review.py` —— 出图前的提示词把关（A77-A78，2026-09-18）

用户加这个环节的初衷是**审美**（"免得跑出一些符合基本要求但是图片审美完全不合格的图片"），
所以分工写死成两半：**可枚举的错 → 代码（$0、确定性、一票否决）**；
**审美判断与改写 → LLM（低于阈值给改写稿，改写稿必须过体检才落地）**。

```
lint_prompts(plan, *, platform, identity) -> {errors, warnings, findings, checked, expected, digest}
prompt_digest(plan)                        # 幂等键：同一版提示词不重复花钱
normalize_prompt_review(raw)               # 宽松规范化审核员输出
select_patches(plan, review, threshold)    # 低于阈值且给了 scene 的才落地
apply_prompt_patches(plan, patches, ...)   # 逐槽位过体检：新增硬伤就拒绝并记账
review_summary(review, threshold)          # 一行摘要（群聊/产物）
```

**体检规则**：`missing_slot`(e) / `missing_intent`(w) / `identity_in_prompt`(e) /
`packaging_prose`(w) / `fake_text_request`(e) / `missing_keep_clear`(e) /
`missing_design_terms`(e，版式·背景·光影各≥1) / `missing_palette`(w) / `negation_pile`(w) /
`magic_words`(w) / `contradiction`(w) / `prompt_too_short|too_long`(w) / `forbidden_claim`(e) /
`white_required_bg`(e) / `duplicate_prompts`(w，3-gram Jaccard>0.6)。

**安全边界（写在代码里，不靠模型自觉）**：改写**只能换 `prompt`（画面段）**，
`must/avoid/keep_clear/palette/number/intent` 来自配置改不掉；被拒绝的改写与原因进
`refine_rejected`，前端与产物都展示。

### `product_identity.py` —— 身份词治理与品牌色板（A70-A71）

- `identity_terms(identity)`：品牌/品名/规格/认证 + 分词变体（剔"德国/原裝"等停用词与
  <3 字符 ASCII 片段）→ 供体检与提示词清理；
- `normalize_palette(raw)` / `palette_summary(palette)`：**品牌色板**（主色/辅色/背景色 + 出处），
  由分析员在同一次视觉调用里取样（零额外成本）→ 进身份卡 → 注入提示词，并作为本地排版
  文字层的默认主/辅色（用户显式改过 `typography` 时以用户为准）。

### `image_compose.py` —— 排版自适应与保真（A73）

- **不再把 1:1 底图缩到 1200**：有底图就保持底图分辨率（`DEFAULT_INFO_CANVAS=2048` 仅用于无底图）；
- `_draw_text` **自适应**：整行 → 二分断点换行（≤2 行）→ 逐档缩字号（下限 0.7）→ 最后才截断
  并记账（实测修复："6 条卖点全被截"与页脚"每粒 0.391g"被截成"每粒 0.3"）；
- 条目区加**文字底卡**（`PALETTE["card"]`），设计底图上也能读清；
- `LAYOUT_CAPACITY` 按各模板行高算出真实容量（`step_list` 5 / `cert_badge` 4，其余 6），
  超出时在 `meta.truncated` 里说明省略了几条。

### `core/platforms.py` —— 槽位契约、背景策略与解析缓存（A70）

- **槽位契约**：`slot_intent/slot_design/slot_must/slot_forbid/slot_keep_clear/slot_spec`、
  `slot_contract_block()`、`platform_slot_table()`（**编号槽位表**，主图在前详情图在后）、
  `slot_table_block()`；`platform_style_block()` 渲染编号槽位表 **+ 详情槽位 + 设计方向块**；
- **背景策略**：`background_policy(platform)` → `white_required | white_preferred | design_allowed`
  （默认 Amazon 强制纯白、京东/1688 白底优先、其余允许设计底）——用户实测质疑"很多商品图
  都不是白底的啊？"后落地；**本地体检按该策略显式判定**是否要求白底，不再猜 `#FFFFFF` 字符串；
- **解析缓存**：`_platforms_doc()` 按 `(路径, mtime_ns, size)` 缓存 `platforms.yaml` 解析结果
  （配置改了立刻生效）。此前每次访问都重解析 15KB（~27ms），而槽位契约把调用次数放大了
  几十倍 → 渲染一次平台规范块 **10.8s**；缓存后 **0.02s**，后端全量测试 427s → 306s。


### `slot_copy.py` + `image_compose.py` —— 信息图（A61-A63，2026-09-16）

用户追问"为什么生成的全是白背景＋商品的图……我要『纯商品图+成分图+商品面向人群图片+商品效果列举图』"
之后补齐的一环。`config/platforms.yaml → slot_catalog` 给每个槽位声明 `kind`：

| kind | 含义 | 产出 |
|------|------|------|
| `photo` | 纯摄影（纯商品图/场景/细节/笔记图） | 生图模型直接出成品（画面无文字） |
| `info` | **信息图**（卖点/功效/成分/人群/规格/用法/资质/对比） | 模型只出**无字底图 + 留白版式**，文字由 `image_compose` 本地绘制 |

```
build_slot_copy(slot_id, *, identity, analysis, user_copy)
    → {title, items, footer, blocked, reason, sources}
render_info_image(base_bytes, copy, *, layout, size) → (JPEG 字节, meta)
compose_info_image(base_bytes, copy, slot)           # 按槽位取 layout/aspect
available_fonts() / layout_options() / palette_preview()
```

- **文案只从已确认事实取**：身份卡（品牌/品名/规格/认证）+ 分析结果（成分/卖点/人群）+
  包装可见文字转录 + 用户填写；
- **缺依据 → `blocked` + 可执行原因**（"包装正面看不到成分表：请上传包装背面/成分表照片"），
  **绝不编造**（本商品分析员原话："未見成分表 → 嚴禁臆造奶薊草、水飛薊…"）；
- **广告法违禁词过滤**（极限词/疗效词，**简繁并列** —— 实测该商品分析结论是繁体，
  只写简体会漏掉"治療/無副作用"）；
- 标记为"不可见/待确认"的素材一律不进文案；
- 8 套版式（卖点/功效/成分/人群/规格/用法/资质/对比），中文字体自动探测
  （微软雅黑 → 思源黑体 → PingFang；都没有时返回可读原因而不硬画），文字自动截断不溢出，
  页脚放品牌/规格/认证；**`blocked` 时原样返回底图、一个字都不画**。

**链路**：`image_gen._compose_info_slot()`（生图后、落盘前）→ 合成结果作为交付物，
保留无字底图 URL 供重排；`set_plan_coverage()` 把 blocked 槽位单列 `blocked_slots`，
前端与协调者都会看到"缺素材·未生成"（**不是**"缺生成"——协调者明确被告知
"重新邀请生图员没有用，需要用户补素材"，避免又白跑一轮烧钱）。

**零成本预览**：`python scripts/render_info_samples.py --all-slots [--user-copy-file facts.json]
[--typography-file style.json]` 拿任意会话的真实商品信息只重排文字层（调字号/配色/版式不必
重新生图），产物含 `manifest.json`。

**排版样式可设置**（`config/image.yaml → typography`，设置页「🖼️ 生图质量策略」四个控件）：

| 键 | 取值 | 说明 |
|----|------|------|
| `font_scale` | 0.6–1.8 | 字号倍率（1 = 默认；标题/条目/小字按比例缩放） |
| `max_items` | 1–8 | 一张信息图最多画几条（超出会在 `meta.truncated` 里说明省略了几条） |
| `brand_color` / `accent_color` | `#RRGGBB` | 标题条/主色块 与 次色块/编号方块 |
| `show_footer` | true/false | 页脚是否画"品牌｜规格｜认证" |

`render_info_image` 的返回 meta 带 `typography`（生效参数）与 `truncated`（**哪些文字被截断/
省略了几条**）—— 用户据此缩短文案或调大字号/条目上限，而不是让排版悄悄丢内容。
（自适应排版与分辨率保真见上文「`image_compose.py` —— 排版自适应与保真（A73）」。）

---

## 风格档案库 / 风格词库（A79-A96，2026-09-18；A97-A107，2026-09-20）

用户 2026-09-18（第 1 条反馈）："我发现生成的图片质量太差了，缺少了该有的商品图审美，
这跟提示词生成员的问题脱不开关系"；随后两次更正：审美标准是**平面设计**（"背景不一定非要
'真实'，而是要有一定的高级感"）而不是纪实摄影；白底**不是**全局硬规则。
再后来用户指定了界面模块：「风格词库」（左栏「记忆库」下面）→ 导入照片 + 命名 →
"专门的 agent 帮我分析这组照片的风格"。

2026-09-19/20 用户三条追问推动了 A97-A107：

1. **"我给的是一套图片，那应该不止是单纯的分析图片的美术风格，还有套图的制作习惯"**
   → 新增 `shot_flow` / `shot_roles`（见下"套图结构"）；
2. **"为什么限定只能输入 6 张图片？"** → 上限单一来源化并放到 20（单批 12，超出分批）；
3. **"是否有在风格词库那里限定启用一个风格另一个风格自动停用？"** → 此前**没有**，
   实测每张图被两套互相矛盾的档案同时指挥（你的风格 + 一套内置原型）；现在
   **一轮会话只用一套风格词**（radio + 会话锁 + 严格模式）。
   > 口径说明（用户 2026-09-20 更正）：单位是**一套**风格词 —— 词库里一条记录携带的那组字段
   > （`style_words` + 背景/构图/光影/材质/元素/用色分工/留白/禁止项 + 套图结构），
   > **一起用**才构成这套风格；不是"一个词"。

### `style_library.py` —— 内置档案、逐槽位检索与渲染

- **档案 = 可复用的设计决策**（背景处理 / 构图骨架 / 光影做法 / 材质 / 元素预算 / 留白 / 禁忌），
  与"每次靠模型自由发挥"正相反：同一商品两次跑出来的画面应当有稳定的审美下限；
- **一轮会话一套风格词**（A97）：`_resolve_active_user()` 先定出**该轮生效的那一套**用户词条
  （会话锁 → 品类命中 → 最近更新 → id 兜底），`_pick_for_slot()` 再逐槽位决定——
  *覆盖该槽位*（有 `shot_roles` 就按它判，没有则视为通用）→ **只注入它**；
  *覆盖不到* → 严格模式（`max_entries ≤ 1`，默认）**返回空**（该张靠平台槽位契约兜底），
  补位模式（≥2）才允许内置原型补 1 套；
- **会话锁**：`session_style_lock(session)` 读 `task.style_entry_id` →
  `artifacts.prompts.style_refs.locked_entry_id`（首次检索时落下），提示词生成员 /
  审核优化员 / 引擎体检全部传 `locked_entry_id` —— 否则中途切换词库启用项会让同一套图
  前后用两个风格。锁定的词条不可用时**回落内置原型并如实说明**（不静默换一条）；
- `applies_to` 支持 `kinds/slots/not_slots/categories/platforms/requires_policy`；
- **与槽位契约零冲突**：`entry_conflicts()` 检测"档案的正向描述 vs 该槽位的 `forbid`"，
  命中处带否定词（"无道具"）或委派措辞（"文字由本地排版"）不算冲突；**逐张做法只与它自己
  那个槽位比对**（跨槽位会把"参考第2张"的做法误当成首图的违规）；信息图槽位的
  「文字/数字/字母」不再参与本检查（实测一条用户词条因此报了 **36 处**误报，把真问题埋掉；
  这类"要求画面里出现文字"由 `prompt_lint` 在提示词层面精确拦截）；
- **事实中立三道闸**：① 提示词层禁止输出品牌/成分/认证/色值；② 加载层 `scan_entry()` 命中即
  丢弃并记账（**不静默**）；③ 注入层同一套校验。`sanitize_entry()` 供用户词条用：
  **保内容、剔越界、如实记账**（局部越界很常见，整条丢掉会让用户白分析一次）；
  其中**槽位角色名先豁免**（「成分配方图」「资质认证图」是系统自己的受控词汇，
  不是"这张照片上的事实"——否则用户写"再讲成分配方图"会被整段剔掉）；
- **渲染纪律**：`### 档案定义`（每条档案只写一次）+ `### 参考套图的编排习惯`（只有带
  `shot_roles` 的档案才有）+ `### 逐张对应`（第N张→哪条→该张做法/未覆盖标注）。
  按槽位各写一遍会变成 3000+ 字重复文本（8 个信息图槽位命中同一条档案）——A96 实测踩到并改掉；
- **配置缓存**：`_doc()` 按 `(路径, mtime_ns, size)` 缓存（与 `core/platforms.py` 同一教训：
  检索把访问次数按「槽位×档案」放大，每次重解析会把渲染拖到秒级）；
- **用户词条的停用状态**存在数据目录（`data/style_library/builtin_disabled.json`）——
  程序**不得**改写带注释的 `config/*.yaml`（`yaml.safe_dump` 会吃掉全部注释，本仓库已有教训）。

**套图结构（A98，用户 2026-09-19："我给的是一套图片"）**

| 字段 | 含义 | 谁写 |
|---|---|---|
| `shot_flow` | 整套的叙事顺序（一句话） | 仅「风格档案员」分析用户照片后写入用户词条 |
| `shot_roles[]` | `{number, slot, treatment}`：第几张 / 什么角色（槽位 id）/ 与共同美术不同在哪 | 同上（界面可逐张修正） |

- **角色词不由模型写**：模型只从 `main_white=纯商品图；…` 清单里挑 `slot`，界面里的
  "纯白商品图/成分配方图"由 `slot_label()` 派生（自由写角色名会命中事实词表「成分」而被清洗掉，
  且与平台槽位目录脱节）；
- **序号必须与照片对得上**：`normalize_shot_roles(..., image_count=)` 保留合法且未占用的序号，
  非法/重复的补空位，"第2张没识别出来"不会把第3张挤成第2张（那会让提示词按错的照片写画面）；
- 覆盖差 `sequence_coverage()` 按 `entry_id` 分组给出 `matched / missing_in_ref / extra_in_ref`，
  **只进预览与产物快照，不进提示词**。

```
load_library(tenant_id=None, *, include_user=True) → {entries, anchors, disabled, dropped, defaults}
select_by_slot(slots, *, analysis, platform, policy, max_entries, enabled, library,
               tenant_id, locked_entry_id) → {…, active_entry_id, locked_entry_id, strict_single}
session_style_lock(session) / covered_slots(entry) / normalize_shot_roles(raw, *, image_count)
render_slot_block(selection, *, audience="gen"|"review") / render_anchor_block(anchors, *, audience)
style_block_for(*, slots/selection, analysis, platform, audience, include_anchors)
usage_snapshot(selection) / describe_selection() / sequence_coverage() / entry_conflicts()
similar_entries() / builtin_ids() / library_stats() / sanitize_entry(raw) / style_plain_text(entry, slot_id)
```

**注入成本**：套图结构段 ≤900 字/次（约 500 token），且**只有带 `shot_roles` 的档案**才产生；
没有结构的档案（含全部内置原型）渲染块与 A96 版本逐字节一致。

**锚点（anchors）** = 用户"就要这种感觉"的**文字判词**（`taste_verdict` + `reward_points` +
`avoid_points`），作为提示词审核优化员与成图审查员的**打分准绳**（优先于内置标准）。
**默认是空的**：不由程序编造用户的审美。

### `style_store.py` —— 用户词条（照片 → 文字档案的落盘）

```
data/style_library/entries.json       # 用户词条（原子替换 + 模块级写锁）
data/style_library/builtin_disabled.json   # 内置档案的启停（配置只读）
data/style_library/stats.json         # 采纳计数（best-effort，参考值）
data/style_library/<id>/photo_N.jpg + thumb_N.jpg
```

**安全边界（本模块存在的意义）**：用户导入的照片很可能是**别人家的包装**。图片直进生图链路会把
别人包装上的文字/图案带进本商品（两次真实事故：`水飞蓟` 抄进成分未确认的商品、`DEFOEBUENA®`
被编成 `NUTRIVA®`），还会挤占实拍参考额度。因此：

- 照片只落**本模块自己的目录**，**绝不写 `data/inputs/`**，也**不 import** `reference_images` /
  `vision_payload`（有静态扫描测试钉住）；
- 照片只有两个用途：给「风格档案员」做**一次**风格分析、给界面显示缩略图；
- 分析结果经 `sanitize_entry()` 清洗后入库，剔除项如实展示（"已剔除 N 处品牌文字"）。

**张数与增删（A99-A101，用户 2026-09-19："为什么限定只能输入 6 张？"）**

- `MAX_PHOTOS = 20`、`VISION_BATCH_SIZE = 12`（**单一来源**：接口与档案员都 import 它们，
  前端由 `GET /api/style-library → stats.limits` 下发）——"6"此前在四处各写一遍且无任何凭据
  （用户实测 6 张 1 次调用 $0.000932）；
- `add_photos()` **只 append**（序号不变 → 已识别的逐张角色仍然对得上）；
  `remove_photo()` 会重排 `photo_N.jpg` → **清空 `shot_roles` 并回报 `cleared_roles`**
  （宁可让用户再点一次「再分析」，也不留下"第2张其实是原来第3张"的错误映射）；
- 收割阈值随批数放宽：`analyzing_timeout_s(张数)` = `600 + (批数-1)×240` 秒
  （分批 = 多次调用；固定 600s 会在 20 张时误判"分析中断"，界面先显示失败、随后又翻回 ready）。

**一轮会话一套风格词（A97）**

- `set_entry_enabled()` / `disable_other_entries()`：**启用一套 → 同租户其他用户词条自动停用**
  （radio），响应带 `auto_disabled` 名单；分析成功时才切换（失败则旧的照常生效，不留空档）；
- 界面上的"启用"因此等于"选它作为我现在的套图风格"；内置档案**不参与** radio
  （它们是逐槽位通用原型，不是"一整套风格"）。

其他要点：读取走 mtime 缓存（每次提示词阶段都会读一遍）；写入持**模块级**锁 + `os.replace`
原子替换；`data_root()` **在使用时**取（模块级固化会让测试写进真实 `data/`）；每租户 ≤50 条。

### `pricing.py` —— 成本金额的"事实优先"口径（A94）

用户质疑："不同的模型的花费是不同的……除非每次模型商修改价格的时候都及时更新，不然会出现很大的误导"。
核实后确认质疑成立（当时的 `$0.04/张` 来自 `_estimate_cost` 的**兜底常量**，与方舟实际计费无关）：

- **用量是事实**（路由/模型/能力/张数/tokens/耗时，来自 provider 响应）→ 永远显示；
- **金额是估算** → 只在"有来源"时显示：`供应商回报` → `用户填写/内置参考价/实测标定`；
  **未标定 → `amount=None`**，界面显示"未标定（N 张图）"，绝不拿兜底常量编一个数字；
- 价格表 `config/pricing.yaml`（实例级、程序写）+ `observed_models()` 从审计日志聚合
  "你实际用过的模型"，用户填单价即可；`calibrate()` 用"实际花费 ÷ 实际用量"反推单价；
- 前端 `frontend/src/cost.js` 的 `formatCost(amount, meta)` 是**全站唯一金额口径**。

