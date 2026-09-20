"""把"生成图记录"转成视觉模型能吃的 content parts（A31）

实测事故：审查员/合规审查员此前只认 `base64_data`

```python
for img in images[:3]:
    b64 = img.get("base64_data", "")
    if b64 and len(b64) > 100:
        user_content.append({... f"data:image/png;base64,{b64}"})
```

而**真实 Provider 全部只返回 URL**（方舟 Seedream / DALL·E / FLUX 都是
`{"image_url": "https://…"}`，且方舟返回的是 `.jpeg`）——于是审查员永远收不到图，
只能回 `NO_IMAGE_ACCESSIBLE`，会话必然走进人工审查。这里统一支持四种来源：

1. `base64_data`（内联，mime 由魔数嗅探，不再写死 png）
2. `image_url` 是 `data:` URI（原样透传）
3. `saved_path`（引擎已自动落盘到输出目录，**本机文件，最稳**）
4. `image_url` 是 http(s) 远程地址（下载一次转 base64；带超时与大小上限）

失败不抛异常：把可读原因收集到 notes，交给调用方决定（审查员据此提示模型"哪张拿不到"）。
"""

import base64
import binascii
from pathlib import Path

MAX_IMAGES = 3
MAX_BYTES = 8 * 1024 * 1024          # 单图上限（超限不入模，避免撑爆上下文）
DOWNLOAD_TIMEOUT_S = 30.0

# 魔数 → MIME（顺序敏感：先长后短）
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def sniff_mime(data: bytes) -> str:
    """按魔数判定图片 MIME；未知（含 SVG/无数据）回落 image/png"""
    if not data:
        return "image/png"
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:512].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "image/svg+xml"
    return "image/png"


def _data_uri(data: bytes) -> str:
    return f"data:{sniff_mime(data)};base64,{base64.b64encode(data).decode()}"


def inline_data_uri(b64: str) -> str:
    """裸 base64 → 带**嗅探出的** MIME 的 data URI

    实测踩坑：上传图是 PNG，而分析员/风格拆解员写死 `data:image/jpeg;base64,…`
    —— 视觉模型收到错误 MIME。这里按魔数判定，非法 base64 原样透传（部分端点的
    字段本就自带前缀）。
    """
    text = str(b64 or "").strip()
    if not text:
        return ""
    if text.startswith("data:"):
        return text
    try:
        return _data_uri(base64.b64decode(text, validate=True))
    except (binascii.Error, ValueError):
        return f"data:image/png;base64,{text}"


def _local_path(rel_path: str, output_root: Path) -> Path | None:
    """把 saved_path 解析为输出目录内的真实文件（越界/不存在返回 None）

    仅允许落在输出根内：saved_path 来自产物字段（可被上游污染），
    不校验就能用 `../../../etc/passwd` 把宿主机任意文件读进模型上下文。
    """
    text = str(rel_path or "").strip()
    if not text:
        return None
    try:
        root = output_root.resolve()
        candidate = (output_root / text).resolve() if not Path(text).is_absolute() else Path(text).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


async def _download(url: str) -> bytes:
    import httpx

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        data = resp.content
    if len(data) > MAX_BYTES:
        raise RuntimeError(f"图片过大（{len(data) / 1024 / 1024:.1f}MB > {MAX_BYTES / 1024 / 1024:.0f}MB）")
    return data


async def image_parts(
    images,
    limit: int = MAX_IMAGES,
    *,
    output_root: Path | None = None,
    downloader=None,
    labelled: bool = False,
) -> tuple[list[dict], list[str]]:
    """返回 `(content_parts, notes)`

    content_parts: `[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,…"}}]`
    notes: 人类可读的说明/失败原因（调用方可作为文本 part 附给模型）

    `labelled=True`：在每张图**之前**插一段文字（`prompt_name`，如"第1张"）。
    多图任务（一组参考照片逐张对应）必须标明顺序，否则"第几张是什么"只能靠位置猜 ——
    「风格档案员」的 `shot_roles` 就靠它对齐。默认 False（审查员/合规审查员行为不变）。
    """
    parts: list[dict] = []
    notes: list[str] = []
    if not isinstance(images, list):
        return parts, ["未提供图片记录"]

    candidates = [img for img in images if isinstance(img, dict)]
    if len(candidates) > limit:
        notes.append(f"共 {len(candidates)} 张，仅取前 {limit} 张送入视觉模型")

    if output_root is None:
        from src.core.config import output_root as _root
        output_root = _root()
    if downloader is None:
        downloader = _download

    def _emit(url: str, name: str) -> None:
        if labelled:
            parts.append({"type": "text", "text": f"{name}\n"})
        parts.append({"type": "image_url", "image_url": {"url": url}})

    for index, img in enumerate(candidates[:limit], start=1):
        name = str(img.get("prompt_name") or f"第 {index} 张")

        inline = str(img.get("base64_data") or "").strip()
        if inline:
            try:
                _emit(_data_uri(base64.b64decode(inline, validate=True)), name)
                continue
            except (binascii.Error, ValueError):
                # 不是合法 base64：按原样透传（部分端点的字段本就带前缀）
                _emit(f"data:image/png;base64,{inline}", name)
                continue

        url = str(img.get("image_url") or "").strip()
        if url.startswith("data:"):
            _emit(url, name)
            continue

        saved = _local_path(str(img.get("saved_path") or ""), output_root)
        if saved is not None:
            try:
                data = saved.read_bytes()
            except OSError as exc:
                notes.append(f"{name}：本地文件读取失败（{exc}）")
            else:
                if len(data) > MAX_BYTES:
                    notes.append(f"{name}：本地文件过大（{len(data) / 1024 / 1024:.1f}MB）")
                else:
                    _emit(_data_uri(data), name)
                    continue
        elif img.get("saved_path"):
            notes.append(f"{name}：落盘路径不可用（{img.get('saved_path')}）")

        if url.startswith(("http://", "https://")):
            try:
                data = await downloader(url)
            except Exception as exc:  # noqa: BLE001 — 单张失败不影响其余图片
                notes.append(f"{name}：图片下载失败（{type(exc).__name__}: {exc}）")
            else:
                _emit(_data_uri(data), name)
            continue

        notes.append(f"{name}：没有可用的图像数据（无 base64 / 落盘文件 / 可访问 URL）")

    return parts, notes


def reference_image_parts(session, limit: int = 1) -> tuple[list[dict], list[str], list[str]]:
    """用户上传的**真实商品图** → content parts（给审查/合规做"还原度"基准）

    实测事故：审查员只收到生成图，**没有原图** —— 于是"商品还原度"这个维度根本没有基准，
    它只能靠常识猜（那次猜中了被臆造的 `NUTRIVA®`，但这不可靠）。把原图一起送进去，
    它才能逐项比对品牌文字/图案/规格/认证。

    Returns: `(parts, notes, source_labels)`
    """
    from src.harness.reference_images import reference_sources, to_data_uri

    parts: list[dict] = []
    notes: list[str] = []
    labels: list[str] = []
    sources = reference_sources(session)[:max(1, int(limit))]
    if not sources:
        return parts, ["没有可用的上传原图（参考图为空），本次无法做还原度比对"], labels
    for index, source in enumerate(sources, start=1):
        uri, note = to_data_uri(source)
        if uri:
            parts.append({"type": "image_url", "image_url": {"url": uri}})
            labels.append(f"图{index}")
            if note:
                notes.append(f"图{index}（上传原图）：{note}")
        else:
            notes.append(f"图{index}（上传原图）：{note}")
    return parts, notes, labels
