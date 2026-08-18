"""统一日志 — 使用标准 logging，兼容 structlog

所有模块通过 get_logger(__name__) 获取日志器。
日志级别由 ECOMM_LOG_LEVEL 环境变量控制（默认 INFO）。
"""

import logging
import os
import sys
from datetime import datetime, timezone


# ── 全局初始化 ──

def _setup_logging():
    level_name = os.getenv("ECOMM_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    # 格式: [2026-08-05T14:30:00Z] [INFO] [module] message
    fmt = logging.Formatter(
        "[%(asctime)s.%(msecs)03dZ] [%(levelname)-5s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    # 使用 UTC 时间
    fmt.converter = lambda *args: datetime.now(tz=timezone.utc).timetuple()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)

    root = logging.getLogger("ecommerce_harness")
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False


_setup_logging()


def get_logger(name: str) -> logging.Logger:
    """获取日志器

    用法:
        from src.core.logging import get_logger
        logger = get_logger(__name__)
        logger.info("task started", extra={"session_id": sid})
    """
    # name 去掉公共前缀
    short = name.replace("src.", "").replace("agents.", "agent/").replace("providers.", "provider/").replace("harness.", "harness/").replace("chat.", "chat/").replace("core.", "core/")
    return logging.getLogger(f"ecommerce_harness.{short}")


class _StructuredAdapter(logging.LoggerAdapter):
    """自动将 extra 字段注入日志消息"""

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        if extra:
            parts = [f"{k}={v}" for k, v in extra.items()]
            msg = f"{msg} | {' '.join(parts)}"
        return msg, kwargs


def get_structured_logger(name: str) -> _StructuredAdapter:
    logger = get_logger(name)
    return _StructuredAdapter(logger, {})
