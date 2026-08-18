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


async def _webhook_notify(inputs: dict) -> dict:
    """M4 出站连接器：向外部 URL 发送 JSON 通知（best-effort，不因失败中断流程）

    inputs: {url, event, payload}
    SSRF 防护（审计修复）：仅 http/https；拒绝回环/私网/链路本地/保留地址
    （IP 字面量直接判，主机名解析后判——解析失败放行，由请求自然报错）。
    """
    import httpx

    url = str(inputs.get("url", "")).strip()
    if not url:
        return {"ok": True, "status_code": 0, "error": "", "note": "url 为空，跳过通知"}
    event = inputs.get("event", "workflow_event")
    payload = inputs.get("payload", {})

    try:
        error = _validate_notify_url(url)
        if error:
            return {"ok": False, "status_code": 0, "error": error}
    except Exception as e:
        return {"ok": False, "status_code": 0, "error": str(e)[:200]}

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, json={"event": event, "payload": payload})
        return {"ok": resp.status_code < 400, "status_code": resp.status_code,
                "error": "" if resp.status_code < 400 else f"HTTP {resp.status_code}"}
    except Exception as e:
        # 通知类工具 best-effort：失败返回结构化错误而不抛出
        return {"ok": False, "status_code": 0, "error": str(e)[:200]}


def _validate_notify_url(url: str) -> str:
    """SSRF 校验：非法返回错误信息，合法返回空字符串"""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"仅支持 http/https 协议: {url[:50]}"
    host = parsed.hostname or ""
    if not host:
        return "URL 缺少主机名"

    def _blocked(ip) -> bool:
        return (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast)

    try:
        ipaddress.ip_address(host)  # IP 字面量
        if _blocked(ipaddress.ip_address(host)):
            return f"禁止访问内网/回环/保留地址: {host[:50]}"
        return ""
    except ValueError:
        pass

    # 主机名：解析后检查（解析失败放行，请求会自然失败）
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return ""
    for info in infos:
        if _blocked(ipaddress.ip_address(info[4][0])):
            return f"禁止访问内网/回环/保留地址: {host[:50]}"
    return ""


register_tool("validate_image", _validate_image)
register_tool("post_process_images", _post_process_images)
register_tool("webhook_notify", _webhook_notify)
