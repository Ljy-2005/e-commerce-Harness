# Storage — 文件持久化

> 覆盖: `src/storage/checkpoint.py`、`data/` 目录布局

## 功能

会话级 JSON 文件持久化（checkpoint），无外部数据库依赖。

- **写入时机** — 群聊每轮结束、上下文压缩前、人工决策后（`chat/session.py` 与 `chat/engine.py` 调用）
- **原子写**（第三轮审计 B1-7）— 同目录临时文件 + `fsync` + `os.replace`；中断/并发只会留下临时文件，旧 checkpoint 始终完整可读（此前 `open("w")` 截断 + 流式 dump，并发实测 4/40 次 JSONDecodeError）
- **崩溃恢复** — 启动时扫描 `<data_root>/checkpoints/*.json`，把非 completed/failed 的会话标记为 failed 恢复进内存（上限 `MAX_CHECKPOINT_RESTORE`）
- **磁盘回收**（第三轮审计 B1-8）— 启动时 `cleanup_checkpoints(会话 TTL)` 回收超期终态 checkpoint；会话被惰性 TTL 驱逐时同步删除对应文件（此前只清内存，实测累积 1689 个文件 / 56 MB）
- **会话删除** — 同步删除对应 checkpoint 文件

## API

```
save_checkpoint(session_id, state) → None       # 原子写入 <data_root>/checkpoints/{id}.json
load_checkpoint(session_id) → Optional[dict]    # 不存在/损坏返回 None
delete_checkpoint(session_id) → None            # 异步（to_thread）
delete_checkpoint_sync(session_id) → None       # 同步（SessionManager 惰性驱逐用）
cleanup_checkpoints(ttl_hours, now=None) → {"scanned","removed","kept"}   # 磁盘回收
parse_timestamp(value) → Optional[float]        # ISO 字符串/datetime → epoch 秒（B1-9）
```

回收规则（**与内存 TTL 驱逐同口径**）：超期且非进行中——created（从未启动）/ 终态 /
未知状态 / 损坏文件——即删；`running` 与 `waiting_human` 及未超期一律保留
（绝不误删可能在跑的会话）；失败写入遗留的 `*.json.tmp` 超期 → 删；
`ttl_hours <= 0` → 不回收（配置语义 0 = 永不过期）。

## 目录可重定向（第三轮审计 B2-18）

`data/` 与 `config/` 的基准目录均可经环境变量重定向，测试据此把落盘隔离到 tmp：

| 变量 | 作用 | 默认 |
|------|------|------|
| `ECOMM_DATA_DIR` | checkpoint / 审计 / 记忆 / workflow.db 的根 | `<项目根>/data` |
| `ECOMM_PROJECT_ROOT` | 配置基准（`config/**`，含模板目录） | 项目根 |

路径一律**使用时解析**（不得在模块级固化成常量，否则 import 期就锁定真实目录）。

## data/ 目录布局

```
data/                               # 可用 ECOMM_DATA_DIR 重定向
├── checkpoints/    # 会话状态 JSON（会话级，checkpoint.py；*.json.tmp 为原子写临时文件）
├── workflow.db     # 工作流 SQLite（job/step/事件/批次，src/workflow/job_store.py）
├── audit/          # 审计日志（按日期分文件，src/harness/audit_logger.py）
└── memory/         # Agent 记忆（JSONL，src/harness/agent_memory.py；模块级写锁串行追加）
```

## 生成图输出（`image_export.py`）

生成图**自动落盘**（此前只以 base64 存在内存/checkpoint，`deploy/` 声明的
`output/` 卷没有任何代码使用）：

```
{输出根}/{租户}/{会话ID}/{平台}_{品类}_{序号}.{ext}
```

- **输出根**：`ECOMM_OUTPUT_DIR` 环境变量 → `config/output.yaml` 的 `dir` → `<项目根>/output`
  （相对路径按项目根解析；设置页可改，env 供给时设置页只读）。见 `config.output_root()`；
- **扩展名按魔数判定**：PNG/JPEG/GIF/WEBP/SVG——Mock 出的是 SVG 占位图，不能被写成 `.png`；
- **三种图像来源**：`base64_data`（内联）→ `image_url` 为 `data:` URI → `image_url` 为远程
  URL（真实 Provider 常见，下载一次后落盘）；
- **时序**：聊天引擎与工作流引擎在 artifacts 更新后调用（工作流用 `job_id` 作会话段）；
  同序号重跑 = 同名覆盖，磁盘上始终是该任务最新的那张图；
- **容错**：单张失败不影响其余，异常只告警，**绝不影响生成任务**；
- 落盘相对路径（POSIX 风格）回写到 image 记录的 `saved_path`，供界面显示与下载。

API（详见 `docs/modules/api.md`）：单张 `GET /api/sessions/{id}/images/{index}/download`、
整会话 `GET /api/sessions/{id}/export`（ZIP），均按租户隔离。

## 与工作流持久化的关系

workflow job 内部 `group_chat` 节点仍写会话 checkpoint（双保险），步骤级状态走 SQLite 事件溯源。两者共存不冲突：checkpoint 管"群聊会话"，SQLite 管"编排步骤"。

## 修改指南

- **更换存储后端** → 保持三个函数签名不变，替换内部实现（如换 PostgreSQL）
- **注意**：checkpoint 是纯文件 IO，高频场景建议包一层写缓冲（当前规模无需）
