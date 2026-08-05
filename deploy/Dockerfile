# ── Stage 1: Build frontend ──
FROM node:22-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --omit=dev
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Production runtime ──
FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash harness

# Python deps (production only)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[prod]" 2>/dev/null || \
    pip install --no-cache-dir fastapi uvicorn[standard] httpx pyyaml typer pydantic python-multipart

# Copy application code
COPY src/ ./src/
COPY config/ ./config/
COPY deploy/nginx.conf ./deploy/

# Copy built frontend from stage 1
COPY --from=frontend-builder /frontend/dist/ ./frontend/dist/

# Create data dirs
RUN mkdir -p data/checkpoints data/audit data/memory output && chown -R harness:harness data/ output/

USER harness
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--log-level", "warning"]
