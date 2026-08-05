# E-Commerce Harness

群聊式多智能体电商商品图生成系统。

7 个 AI Agent 在中心决策者的协调下，像群聊一样协作完成商品图生成任务。

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
uvicorn src.main:app --reload --port 8000

# 或者用 CLI
python -m src.cli run ./product.jpg --platform taobao
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/sessions` | 创建会话 |
| `GET` | `/api/sessions/{id}` | 获取状态 |
| `GET` | `/api/sessions/{id}/messages?since=N` | 增量拉取消息 |
| `GET` | `/api/agents` | 列出所有 Agent |
| `WS` | `/ws/sessions/{id}` | 实时群聊流 |
| `GET` | `/health` | 健康检查 |

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
