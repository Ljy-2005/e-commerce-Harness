# Storage — 文件持久化

> 覆盖: `src/storage/checkpoint.py`、`data/` 目录布局

## 功能

会话级 JSON 文件持久化（checkpoint），无外部数据库依赖。

- **写入时机** — 群聊每轮结束、上下文压缩前、人工决策后（`chat/session.py` 与 `chat/engine.py` 调用）
- **崩溃恢复** — 启动时扫描 `data/checkpoints/*.json`，把非 completed/failed 的会话标记为 failed 恢复进内存（上限 `MAX_CHECKPOINT_RESTORE`）
- **会话删除** — 同步删除对应 checkpoint 文件

## API

```
save_checkpoint(session_id, state) → None       # 写入 data/checkpoints/{id}.json
load_checkpoint(session_id) → Optional[dict]    # 不存在返回 None
delete_checkpoint(session_id) → None
```

## data/ 目录布局

```
data/
├── checkpoints/    # 会话状态 JSONL/JSON（会话级，checkpoint.py）
├── workflow.db     # 工作流 SQLite（job/step/事件/批次，src/workflow/job_store.py）
├── audit/          # 审计日志（按日期分文件，src/harness/audit_logger.py）
└── memory/         # Agent 记忆（JSONL，src/harness/agent_memory.py）
```

## 与工作流持久化的关系

workflow job 内部 `group_chat` 节点仍写会话 checkpoint（双保险），步骤级状态走 SQLite 事件溯源。两者共存不冲突：checkpoint 管"群聊会话"，SQLite 管"编排步骤"。

## 修改指南

- **更换存储后端** → 保持三个函数签名不变，替换内部实现（如换 PostgreSQL）
- **注意**：checkpoint 是纯文件 IO，高频场景建议包一层写缓冲（当前规模无需）
