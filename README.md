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

```bash
# 安装依赖
pip install -e ".[dev]"

# Mock Mode 启动（无需 API Key）
uvicorn src.api.main:app --reload --port 8000

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
