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
            # 2. Agent 执行
            result = await agent.execute(task_brief, session)
            广播 agent_msg

            # 3. 更新 artifacts
            _update_artifacts(session, agent_name, result)

            # 4. 审查重试逻辑
            if agent_name == "审查员" and verdict == "retry" and score < 75:
                反馈 msg → session.messages  # Coordinator 下轮读到

    return session
```

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
update(session_id, state)    # 更新 + 刷新 updated_at
delete(session_id)           # 删除
list_ids() → [str]
```

当前为**内存存储**（dict），后续可替换为 SQLite/Redis。

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
