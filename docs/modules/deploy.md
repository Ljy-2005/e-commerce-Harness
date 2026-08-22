# Deploy — 部署配置

> 覆盖: `deploy/Dockerfile`, `deploy/docker-compose.yml`, `deploy/nginx.conf`, `.env.example`, `pyproject.toml`

## 功能

部署层提供开发和生产环境的容器化方案。

## 文件

### `deploy/Dockerfile`
后端容器镜像，基于 `python:3.12-slim`。

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"
COPY . .
EXPOSE 8000
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `deploy/docker-compose.yml`
开发环境一键启动：

```yaml
services:
  api:
    build:
      context: ..
      dockerfile: deploy/Dockerfile
    ports: ["8000:8000"]
    env_file: [../.env]
    environment:
      - ECOMM_MOCK_MODE=false
      - ECOMM_LOG_LEVEL=INFO
    volumes:
      - ../data:/app/data
      - ../output:/app/output
```

启动: `docker-compose -f deploy/docker-compose.yml up -d`

### `deploy/nginx.Dockerfile` + `deploy/nginx.conf.template`
**多阶段构建**（审计修复部署口径：此前 compose 的 `frontend_dist` 卷无人填充，nginx 服务空目录）：

```
阶段 1 (node:20-alpine)   npm ci + npm run build → dist/
阶段 2 (nginx:alpine)     COPY dist → /usr/share/nginx/html
                          COPY nginx.conf.template → /etc/nginx/templates/（envsubst 渲染）
```

- `nginx.conf.template` 中 `${API_HOST}` 由环境变量注入：docker-compose 下为 `api`（服务名）；本机手动跑容器传 `-e API_HOST=127.0.0.1`
- 手动部署（非容器）仍使用仓库根 `deploy/nginx.conf`（代理 `127.0.0.1:8000`，静态目录需自行放置构建产物）

```
location /api/ → proxy_pass http://${API_HOST}:8000
location /ws/  → proxy_pass http://${API_HOST}:8000 (Upgrade: websocket)
location /     → SPA static files (try_files $uri /index.html)
```

### `pyproject.toml`
项目元数据 + 依赖声明：

```
[project]
dependencies: pydantic, pyyaml, pydantic-settings, structlog, httpx,
              tenacity, fastapi, uvicorn, typer, python-multipart, aiofiles

[project.optional-dependencies]
dev: pytest, pytest-asyncio, pytest-cov, openai, ruff
```

### `.env.example`
环境变量模板。详见文件内注释。每个 Provider 的 Key 变量均已列出，另含平台配置：

| 变量 | 说明 |
|------|------|
| `ECOMM_MOCK_MODE` | 强制 Mock（true/false），缺省按 API Key 自动检测 |
| `ECOMM_LOG_LEVEL` | 日志级别（默认 INFO） |
| `ECOMM_CORS_ORIGINS` | CORS 白名单（默认回退 `config/default.yaml` 的 `app.cors_origins`） |
| `ECOMM_API_KEY` | 全局管理 Key，配置后启用 API 鉴权（不配置则开发模式全开放） |
| `ECOMM_TENANT_KEYS` | 租户独立 Key 引导（`tenant1:key1,tenant2:key2`），配置后持租户 Key 的请求身份绑定该租户；也可经设置页管理（`config/tenant_keys.yaml`） |
| `ECOMM_TENANTS` | 租户注册（`tenant1:pro,tenant2:free`），未配置则只有 default 租户 |
| `ECOMM_WEBHOOK_TOKEN` | 工作流入站回调鉴权（未配置回调端点 503 停用） |

## 开发流程

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key

# 3. 运行测试（Mock Mode，无需 Key）
pytest

# 4. Ruff 静态检查（CI 同款）
ruff check src tests

# 5. 启动开发服务器
uvicorn src.api.main:app --reload --port 8000

# 6. CLI 测试
python -m src.cli run product.jpg --platform taobao
```

## 生产部署流程

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入真实 API Key + ECOMM_API_KEY + ECOMM_CORS_ORIGINS

# 2. Docker 启动（nginx 镜像内含前端构建产物，无需手工填充静态卷）
docker-compose -f deploy/docker-compose.yml up -d --build
# 访问 http://localhost：nginx :80 → 前端静态 + /api、/ws 代理到 api 服务

# 3. Nginx 反向代理（可选）
# 将 deploy/nginx.conf 放入 Nginx sites-enabled/
```

## 修改指南

- **新增 Python 依赖** → 编辑 `pyproject.toml` 的 `dependencies` 或 `dev` 列表
- **新增环境变量** → 编辑 `.env.example`，在对应 Provider 的 `__init__` 中添加 `os.getenv()`
- **修改部署架构** → 编辑 `deploy/docker-compose.yml`（如加 Redis/PostgreSQL 服务）
