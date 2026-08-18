# Logging — 统一日志

> 覆盖: `src/core/logging_config.py`

## 功能

标准库 `logging` 的统一配置（兼容 structlog 迁移），所有模块经 `get_logger()` 获取日志器。

- **UTC 时间戳** — 格式 `[2026-08-05T14:30:00.123Z] [LEVEL] [module] message`
- **级别控制** — `ECOMM_LOG_LEVEL` 环境变量（默认 INFO）
- **结构化扩展** — `extra={...}` 键值对经 `_StructuredAdapter` 自动注入消息尾部
- **命名规整** — `src.agents.analyst` → `ecommerce_harness.agent/analyst`（`src.` / `agents.` / `providers.` / `harness.` / `chat.` / `core.` 前缀映射）

## API

```
get_logger(name) → logging.Logger
get_structured_logger(name) → LoggerAdapter   # extra 键值对 → 消息尾部 " | k=v k=v"
```

## 用法

```python
from src.core.logging_config import get_logger
logger = get_logger(__name__)
logger.info("Agent 注册完成", extra={"count": 9, "names": "..."})
# → [2026-08-16T10:00:00.000Z] [INFO ] [ecommerce_harness.main] Agent 注册完成 | count=9 names=...
```

## 修改指南

- **调整日志格式** → `_setup_logging()` 的 `logging.Formatter`
- **新增日志级别** → 设 `ECOMM_LOG_LEVEL=DEBUG` 启动（不改代码）
- **迁移到 structlog** → 替换 `_setup_logging`（`get_logger` 签名保持，调用方无感知）
