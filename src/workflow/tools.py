"""Workflow 编排层 — 工具节点注册表（确定性操作）"""

import base64
from typing import Any, Callable, Awaitable

ToolFn = Callable[[dict], Awaitable[dict] | dict]

_registry: dict[str, ToolFn] = {}


def register_tool(name: str, fn: ToolFn):
    """注册工具（工具函数签名: async def fn(inputs: dict) -> dict）"""
    _registry[name] = fn


def get_tool(name: str) -> ToolFn | None:
    return _registry.get(name)


def list_tools() -> list[str]:
    return sorted(_registry.keys())


async def run_tool(name: str, inputs: dict) -> dict:
    fn = get_tool(name)
    if fn is None:
        raise ValueError(f"工具 '{name}' 未注册")
    result = fn(inputs)
    if hasattr(result, "__await__"):
        result = await result
    return result or {}


# ── 内置工具 ──


async def _validate_image(inputs: dict) -> dict:
    """校验输入图片（base64 字符串列表）"""
    images = inputs.get("images") or []
    if isinstance(images, str):
        images = [images]
    errors = []
    valid = 0
    for i, img in enumerate(images):
        if not img or not isinstance(img, str) or len(img) < 10:
            errors.append(f"[{i}] 图片数据无效")
            continue
        try:
            base64.b64decode(img, validate=True)
            valid += 1
        except Exception:
            errors.append(f"[{i}] base64 解码失败")
    return {
        "passed": len(errors) == 0 and valid > 0,
        "count": valid,
        "errors": errors,
    }


async def _post_process_images(inputs: dict) -> dict:
    """图片后处理（Phase 1：确定性透传 + 状态标记；真实增强后续接入）"""
    images = inputs.get("images") or []
    processed = []
    for img in images:
        if not isinstance(img, dict):
            img = {"image_url": str(img)}
        item = dict(img)
        item["processing_status"] = "processed"
        processed.append(item)
    return {"images": processed, "count": len(processed)}


register_tool("validate_image", _validate_image)
register_tool("post_process_images", _post_process_images)
