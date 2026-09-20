"""参考图治理 —— 把上传的商品图变成能安全送进生图接口的参考图

用户要求生图必须是"文+图"，其中"图"就是这里负责的：上传原图 → 字节 → 嗅探 MIME →
（必要时降采样）→ `data:` URI。

为什么不能直接把 base64 甩给上游：
- 实测上传原图 2048×2048 **PNG 2.6MB**，转 data URI 约 3.5MB —— 上游对单图与请求体都有上限，
  且每张图都要重新上传一次；先降采样到长边 ≤2048 的 JPEG(q88) 通常只有几百 KB；
- 上传的 base64 来自前端，格式不可信（可能是 PNG/JPEG/WEBP，也可能已经带 `data:` 前缀）；
- 张数要有上限（方舟多参考图 2-14 张；我们默认 4 张足够表达身份），超限要**记录**而不是静默丢。

纯函数 + 可选 Pillow（缺 Pillow 时只做透传，不让生图整体失败）。
"""

import base64
import binascii
import io

from src.harness.vision_payload import sniff_mime

# 单张参考图上限（超限先降采样；降不下来就跳过，避免整个生图请求被上游 400）
MAX_REFERENCE_BYTES = 4 * 1024 * 1024      # 4MB：2.6MB PNG 会先被压到几百 KB
MAX_REFERENCE_SIDE = 2048
JPEG_QUALITY = 88
# 方舟参考图硬限制（单张 < 30MB、宽高 > 14px、像素积 ≤ 3600 万）
UPSTREAM_MAX_BYTES = 30 * 1024 * 1024


def _decode(source: str) -> bytes:
    """把各种形态的"上传图"解析成字节：裸 base64 / data URI（失败返回 b""）"""
    text = str(source or "").strip()
    if not text:
        return b""
    if text.startswith("data:"):
        _, _, payload = text.partition(",")
        text = payload.strip()
    try:
        return base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        return b""


def _shrink(data: bytes) -> tuple[bytes, str]:
    """超过大小上限时降采样为 JPEG；返回 `(字节, 说明)`（Pillow 不可用时原样返回）"""
    if len(data) <= MAX_REFERENCE_BYTES:
        return data, ""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - 本机已装 Pillow
        return data, "未安装 Pillow，无法压缩参考图"
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        image = image.convert("RGB")
        width, height = image.size
        scale = min(1.0, MAX_REFERENCE_SIDE / max(width, height))
        if scale < 1.0:
            image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        shrunk = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 — 压缩失败不该让生图整体失败
        return data, f"参考图压缩失败（{type(exc).__name__}）"
    if len(shrunk) >= len(data):
        return data, ""
    return shrunk, f"已压缩 {len(data) // 1024}KB → {len(shrunk) // 1024}KB"


def to_data_uri(source: str) -> tuple[str, str]:
    """单张上传图 → `(data URI, 说明)`；不可用时 uri 为空串"""
    raw = _decode(source)
    if not raw:
        return "", "图像数据无法解析（base64 非法或为空）"
    if len(raw) > UPSTREAM_MAX_BYTES:
        return "", f"单张超过上游上限 {UPSTREAM_MAX_BYTES // 1024 // 1024}MB"
    raw, note = _shrink(raw)
    return f"data:{sniff_mime(raw)};base64,{base64.b64encode(raw).decode()}", note


def collect_references(sources, *, limit: int = 4) -> tuple[list[str], list[str]]:
    """一批上传图 → `(data URI 列表, notes)`

    - 保持原顺序（第一张通常是包装正面，模型按"图一/图二"引用它）；
    - 超过 `limit` 的部分跳过并记录（不静默丢）；
    - 单张失败不影响其余。
    """
    items = [str(item) for item in (sources or []) if str(item or "").strip()]
    notes: list[str] = []
    if not items:
        return [], ["没有可用的上传图（参考图为空）"]
    if len(items) > limit:
        notes.append(f"共 {len(items)} 张上传图，仅取前 {limit} 张作为参考图")

    uris: list[str] = []
    for index, item in enumerate(items[:limit], start=1):
        uri, note = to_data_uri(item)
        if uri:
            uris.append(uri)
            if note:
                notes.append(f"图{index}：{note}")
        else:
            notes.append(f"图{index}：{note}")
    return uris, notes


def reference_sources(session) -> list[str]:
    """会话里可用于参考的图：内存中的上传图 → 参考图 → **磁盘上的 inputs/**

    顺序即"图一/图二"的编号顺序，提示词里就是按这个顺序引用真实包装的。

    磁盘回退是关键：轻量快照会剔除 `task.product_images`（base64 太占空间），崩溃恢复或
    重启后内存里就没有上传图了 —— 不回退的话 i2i 会**静默退化成纯文生图**，
    又回到"模型编造包装文字"的老问题。
    """
    task = (session or {}).get("task", {}) if isinstance(session, dict) else {}
    sources: list[str] = []
    for key in ("product_images", "reference_images"):
        value = task.get(key)
        if isinstance(value, (list, tuple)):
            sources.extend(str(item) for item in value if str(item or "").strip())
    if sources:
        return sources
    return _sources_from_disk(session)


def _sources_from_disk(session) -> list[str]:
    """从 `output/{租户}/{会话}/inputs/` 读回落盘的上传图（转回 base64 供后续统一处理）"""
    if not isinstance(session, dict):
        return []
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        return []
    tenant_id = str(session.get("tenant_id") or "default")
    try:
        from src.storage.image_export import load_inputs
        items = load_inputs(tenant_id, session_id)
    except Exception:  # noqa: BLE001 — 读盘失败就当作没有参考图（上层会强制虚化文字）
        return []
    return [base64.b64encode(item["data"]).decode() for item in items if item.get("data")]
