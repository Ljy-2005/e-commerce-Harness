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
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
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

### `deploy/nginx.conf`
生产环境反向代理模板。将 API 和 WebSocket 代理到后端，前端静态文件直接服务。

```
location /api/ → proxy_pass http://127.0.0.1:8000
location /ws/  → proxy_pass http://127.0.0.1:8000 (Upgrade: websocket)
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
环境变量模板。详见文件内注释。每个 Provider 的 Key 变量均已列出。

## 开发流程

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 API Key

# 3. 运行测试（Mock Mode，无需 Key）
pytest

# 4. 启动开发服务器
uvicorn src.main:app --reload --port 8000

# 5. CLI 测试
python -m src.cli run product.jpg --platform taobao
```

## 生产部署流程

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填入真实 API Key + ECOMM_CORS_ORIGINS

# 2. Docker 启动
docker-compose -f deploy/docker-compose.yml up -d

# 3. Nginx 反向代理（可选）
# 将 deploy/nginx.conf 放入 Nginx sites-enabled/
```

## 修改指南

- **新增 Python 依赖** → 编辑 `pyproject.toml` 的 `dependencies` 或 `dev` 列表
- **新增环境变量** → 编辑 `.env.example`，在对应 Provider 的 `__init__` 中添加 `os.getenv()`
- **修改部署架构** → 编辑 `deploy/docker-compose.yml`（如加 Redis/PostgreSQL 服务）
