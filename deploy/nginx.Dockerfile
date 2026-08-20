# Nginx 镜像 — 多阶段构建：Node 构建前端产物 → nginx 直接服务
# 修复部署口径：此前 compose 的 frontend_dist 卷无人填充，nginx 服务空目录。

# ── 阶段 1：构建前端 ──
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── 阶段 2：Nginx 服务 ──
FROM nginx:alpine
# 代理配置模板（nginx 镜像启动时经 envsubst 渲染；API_HOST 默认 api=compose 服务名）
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
# 前端产物直接打进镜像
COPY --from=frontend-build /app/dist /usr/share/nginx/html
EXPOSE 80
