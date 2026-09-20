# Chat — 群聊引擎

> 覆盖: `src/chat/engine.py`, `src/chat/session.py`, `src/chat/broadcaster.py`

## 功能

Chat 层是系统的"心"。实现了群聊式多智能体协作的核心循环：

```
                    中心决策者 decide()
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
          invite      invite      done
              │           │           │
              ▼           ▼           ▼
         Agent 执行   Agent 执行   结束
              │           │
              ▼           ▼
         广播消息 ←── 广播消息
              │           │
              └───────────┘
                    │
                    ▼
              下一轮 Coordiantor decide()
```

## 关键类

### ChatEngine (`engine.py`)
群聊主循环。核心流程：

```python
async def run(session: SessionState) -> SessionState:
    coordinator.reset()  # 重置 workflow 索引

    for turn in range(max_turns):
        # 1. Coordinator 决策
        decision = coordinator.decide(session)
        广播 coord_msg

        if decision["action"] == "done":
            session["status"] = COMPLETED
            break

        if decision["action"] == "invite":
            # 2. Agent 执行（stats 回填用量/耗时 → 审计）
            result = await agent.execute(task_brief, session, stats=call_stats)
            广播 agent_msg

            # 3. 更新 artifacts
            await _update_artifacts(session, agent_name, result)

            # 3b. 进度落盘（轻量快照：剔掉上传图 base64）
            await sessions.update(session_id, session, slim=True)

            # 4. 审查门禁（报错/缺 verdict/fail → 转人工；只有 pass 放行）
            ...
            # 5. 质量止损：审查/合规连续失败 N 次 → 终止会话
            halt = self._quality_guard(session, agent_name, result)
            if halt: → status=FAILED + error_history(kind=abort) + 群聊提示
```

**审查门禁与止损**（A33/A37/A43/B1）：

| 情况 | 处理 |
|------|------|
| 审查员报错 / `verdict` 缺失或非法 | 转人工 + `error_history`（**绝不当成 pass 放行**） |
| 逐变体结果 `{"results": [...]}` | `review_normalize` 汇总（分数平均、判定取最严重）后再判定 |
| `verdict="fail"` 或 `retry` 但无有效分数 | 转人工（不静默继续） |
| 审查/合规**连续**未通过达到 `chat.max_consecutive_review_failures` | 会话自动停止（用户可在设置页调整，0 = 关闭） |
| 人工 approve/retry | 计数清零（人已接管） |

**artifacts 映射** (`_update_artifacts`):

| Agent | artifact key | 行为 |
|-------|-------------|------|
| 商品分析员 | `analysis` | 写入 |
| 品类专项分析员 | `analysis` | **合并**到已有 analysis (`.update()`) |
| 提示词生成员 | `prompts` | 写入 |
| 生图员 | `images` | 写入（images 数组） |
| 图像后处理员 | `images` | 覆盖 |
| 审查员 | `review` | 写入 |
| 合规审查员 | `compliance` | 写入 |

### SessionManager (`session.py`)
会话生命周期管理：

```
create(images, product_info, platform, category_hint) → SessionState
get(session_id) → SessionState | None
update(session_id, state, slim=False)   # 更新 + 刷新 updated_at；slim=轻量快照
delete(session_id)                      # 删除
list_ids() → [str]
set_ttl_hours(hours)                    # 设置页改「会话策略」后立即生效
```

当前为**内存存储**（dict）+ checkpoint 落盘；启动时会把 `data/checkpoints/*.json`
**全部**恢复进内存（终态也恢复，否则重启后历史会话从列表消失）。

### Broadcaster (`broadcaster.py`)
WebSocket 广播器：

```
connect(session_id, ws)      # 客户端订阅
disconnect(session_id, ws)   # 客户端断开
broadcast(session_id, msg)   # 向所有订阅者推送 JSON
```

自动清理断连的 WebSocket。

## 群聊消息格式

每条消息的标准结构：

```json
{
  "id": "abc123",
  "turn": 3,
  "timestamp": "2026-07-14T...",
  "role": "coordinator" | "agent" | "system",
  "sender": "中心决策者",
  "action": "invite" | "respond" | "done" | "error",
  "content": { ... }
}
```

## 典型会话示例

```
Turn 0 | System      任务创建: 保健品护肝胶囊, 平台淘宝, 3 张图片
Turn 1 | Coordinator invite @商品分析员
Turn 1 | 商品分析员   respond: {category: "保健品", ...}
Turn 2 | Coordinator invite @品类专项分析员
Turn 2 | 品类专项分析员 respond: {marketing_angles: {...}, ...}
Turn 3 | Coordinator invite @提示词生成员
Turn 3 | 提示词生成员  respond: {main_image: {...}, scene_images: [...]}
Turn 4 | Coordinator invite @生图员
Turn 4 | 生图员       respond: {images: [{...}, {...}, {...}]}
Turn 5 | Coordinator invite @图像后处理员
Turn 5 | 图像后处理员  respond: {images: [...], processed_count: 3}
Turn 6 | Coordinator invite @审查员
Turn 6 | 审查员       respond: {overall_score: 82, verdict: "pass"}
Turn 7 | Coordinator invite @合规审查员
Turn 7 | 合规审查员    respond: {passed: true, risk_level: "low"}
Turn 8 | Coordinator done
```

## 修改指南

- **修改协作策略** → 在 `engine.py` 的 `run()` 方法中修改条件判断
- **增加新artifact** → 在 `_update_artifacts` 中添加 `agent_name → key` 映射
- **调整重试逻辑** → 修改 `run()` 中审查 feedback 的条件（`verdict == "retry" and score < 75`）
- **替换存储后端** → 修改 `SessionManager._sessions` 的实现（dict → SQLite → Redis）

## 商品身份门禁与出图体检播报（A48/A54，2026-09-16）

用户反馈："产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者提醒"。
身份卡是全链路的事实基准，所以引擎在**分析完成后**与**出图完成后**各加了一个环节：

### `_identity_gate(session, result, turn) → bool`

| 情况 | 行为 |
|------|------|
| 识别成功（品牌+品名齐全、`source ∈ {vision, user_confirmed}`、置信度 ≥0.5） | 群聊播报 `✅ 商品身份：品牌 … ｜ 品名 … ｜ 规格 … ｜ 认证 …`，**继续跑**（不打扰） |
| 真实视觉识别失败/低置信度（`source=vision`） | 群聊 `⚠️` 警示 + `error_history(kind="identity")` + **暂停转人工**（默认） |
| `source ∈ {mock, none}`（演示数据/无身份块） | 同样醒目提醒，但**不拦流程**（否则 Mock 模式每条会话都卡在"确认品牌"） |
| `chat.require_identity_confirm: false` | 只提醒不拦 |

命中暂停时消息里带 `hitl: "identity_unconfirmed"` 与可执行指引（approve = 按现有信息继续、
画面文字将虚化交后期贴图；retry = 重新分析商品图）。

`_inject_identity_card(session, brief, agent_name)` 还会把身份卡**前置**进
提示词/生图/审查/合规/品类分析的简报（前置 = 权重最高，与记忆库参考同款处理）。

### `_attach_image_reports(session, result, turn)`

生图完成后本地算指标（零成本），写入 `artifacts.quality_report` 与每张图的 `quality`，
**并广播一条系统消息**（有问题标 `action=error`）：

- `white_bg_ok` / `watermark_free`：白底与「AI生成」水印（实测产物边缘 240/238/235、
  右下区 −30.7/−2.1/−27.7）
- `identity_lost[]`：与参考图**主体相似度过低** → 提示"疑似模型在凭文字想象商品"
  （退化成纯文生图）
- `near_copy[]`：与参考图**整图高度相似且背景未换** → 提示"疑似直接复制、没有按提示词重绘"
  （退化成纯图生图）
- **套图覆盖度**：写 `artifacts.set_plan_coverage`，缺槽位时明说"套图不完整：应有 N 张，缺 …"

> 体检失败（如 Pillow 缺失、坏图）**只告警**，绝不影响出图主流程。
