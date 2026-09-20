# E-Commerce Harness

群聊式多智能体电商商品图生成系统。

9 个 AI Agent 在中心决策者的协调下，像群聊一样协作完成商品图生成任务。

## 架构

```
用户 → POST /api/sessions → ChatEngine 群聊循环
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
                Coordinator  Agent 1    Agent N
                  (决策者)   (分析员)   (合规审查员)
                                │
                    ┌───────────┴───────────┐
                    │   Provider 层          │
                    │   OpenAI / DeepSeek / Mock │
                    └───────────────────────┘
```

## 快速开始

### 一键启动（推荐）

Windows 双击 `start.bat`，或命令行：

```bash
python start.py                 # 启动后端 + 前端，就绪后自动打开浏览器
python start.py --no-browser    # 不自动打开浏览器
python start.py --backend-only  # 只启动后端
python start.py --frontend-only # 只启动前端
```

- 服务已在运行时自动复用，不重复启动；就绪前显示等待进度
- 停止：双击 `stop.bat`（Windows），或在启动窗口按 `Ctrl+C`
- 首次使用前安装依赖：`pip install -e ".[dev]"` + `cd frontend && npm install`
  - 该命令会**自动安装敏感内容防护 git 钩子**；若钩子丢失，手动补装：`python scripts/setup_hooks.py`
  - 注意：本项目要求 **Python ≥ 3.12**，版本不符时 `pip install -e` 会被拒绝

### 手动启动（开发模式）

```bash
# 后端（Mock Mode，无需 API Key）
uvicorn src.api.main:app --reload --port 8000

# 前端（另开终端）
cd frontend && npm run dev    # http://localhost:5173

# 或者用 CLI
python -m src.cli run ./product.jpg --platform taobao
```

## API

完整端点清单见 `docs/modules/api.md` 与 `docs/workflow-design.md` §7。主要分组：

| 分组 | 说明 |
|------|------|
| `POST /api/sessions` | 创建会话（上传商品图，异步群聊） |
| `GET /api/sessions` / `{id}` / `{id}/messages` | 会话列表 / 状态 / 增量消息 |
| `POST /api/sessions/{id}/decision` / `interject` / `ab-test` | 人工决策 / 插话 / A/B 对比 |
| `GET /api/agents` · `GET /api/admin/status` · `GET /health` | Agent / 系统状态 / 健康检查 |
| `GET /api/settings` + 3 个 `POST` | 设置汇总 / API Key / Agent 参数 / 模型映射 |
| `/api/workflows/*` | 工作流：模板画廊/实例化/作业/控制/审批/复刻/批量/报表/导入导出 |
| `/api/webhooks/workflows/{id}/decision` | 入站 Webhook 审批回调 |
| `GET /api/audit` · `GET /api/memory/*` | 审计日志 / 记忆库 |
| `WS /ws/sessions/{id}` · `WS /ws/workflows/jobs/{id}` | 群聊直播（双向插话）/ 工作流事件流 |

## 可用 Agent

| Agent | 能力 | 说明 |
|-------|------|------|
| 中心决策者 | text | LLM 驱动的群聊协调器 |
| 商品分析员 | vision | 识别品类/卖点/风格约束 |
| 品类专项分析员 | vision | 按品类深度分析 |
| 提示词生成员 | text | 生成多平台多模型提示词 |
| 生图员 | image | 调用生图 API 出图 |
| 审查员 | vision | 5 维度质量评分 |
| 合规审查员 | vision | 广告法+平台规范检查 |
| 图像后处理员 | local | 去背景+增强 |
| 风格拆解员 | vision | 拆解参考图风格（一键复刻） |

## 配置

- `config/default.yaml` — 全局配置
- `config/models.yaml` — 能力→模型映射
- `config/agents/*.yaml` — 每个 Agent 一个配置文件
- 环境变量 `ECOMM_` 前缀覆盖 YAML

## 测试

```bash
pytest                    # 全量测试（Mock Mode）
pytest -m real            # 需要真实 API Key
```

## 部署

```bash
docker-compose -f deploy/docker-compose.yml up -d
```

## 敏感内容防护

仓库带有四层防护，防止 API Key 与私人文件被提交或推送：

| 层 | 机制 | 说明 |
|---|---|---|
| 1 | `.gitignore` | 忽略 `config/secrets.yaml`、`.env`、`*.pem`、简历类文件等 |
| 2 | `pre-commit` 钩子 | 提交前扫描暂存区，命中即阻止 |
| 3 | `pre-push` 钩子 | 推送前再查一次，并检查未跟踪文件 |
| 4 | CI + GitHub 推送保护 | 服务端兜底，本地 `--no-verify` 绕过也能拦住 |

手动体检（可选）：

```bash
python scripts/check_secrets.py --tree        # 扫描全部已跟踪文件
python scripts/check_secrets.py --untracked   # 再加未跟踪文件
python scripts/check_secrets.py --history     # 扫描全部提交历史（较慢）
```

误报处理与泄漏应急流程见 [`.github/SECURITY.md`](.github/SECURITY.md)。
