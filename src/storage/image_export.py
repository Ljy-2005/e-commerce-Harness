"""生成图片导出 — 自动落盘与打包下载

背景（用户反馈「没法自己设置生成图片的导出的路径」）：
`deploy/Dockerfile` 早就 `mkdir output` 并挂了 `harness_output` 卷，
但**没有任何代码往里写**——生成图只以 base64 存在内存/checkpoint 里，
前端用 `<img>` 显示，除了模板导出外没有任何下载/导出端点。

布局（用户选定）：`{输出根}/{租户}/{会话ID}/{平台}_{品类}_{序号}.{ext}`

- 输出根见 `src.core.config.output_root()`（env → config/output.yaml → ./output）；
- 序号按会话内 images 列表顺序（1 起）；后处理替换同一列表 → 同名覆盖，
  磁盘上始终是"该任务当前最新的那张图"；
- 图像来源三种都要能落盘：`base64_data`（内联）/ `image_url` 为 data: URI /
  `image_url` 为远程 URL（真实 Provider 常见，需下载一次）；
- 落盘失败**只告警**，绝不影响生成任务本身。
"""

import asyncio
import base64
import binascii
import io
import re
import zipfile
from pathlib import Path

from src.core.config import output_root
from src.core.logging_config import get_logger
from src.harness.vision_payload import sniff_mime

_export_logger = get_logger(__name__)

MAX_FILENAME_PART = 40          # 平台/品类片段长度上限
DOWNLOAD_TIMEOUT_S = 30.0       # 远程图 URL 下载超时

_UNSAFE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _safe_part(value: str, fallback: str) -> str:
    """文件名片段净化：保留中英文/数字/._-，其余（含路径分隔符）压成下划线"""
    text = _UNSAFE.sub("_", str(value or "").strip()).strip("._-")
    text = text[:MAX_FILENAME_PART]
    return text or fallback


def detect_extension(data: bytes) -> str:
    """按魔数判定图片扩展名（Mock 出的是 SVG 占位图，不能一律写 .png）"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:512].lstrip()
    if head[:5].lower() in (b"<svg ", b"<?xml") and b"<svg" in data[:4096].lower():
        return ".svg"
    return ".bin"


def session_dir(tenant_id: str, session_id: str) -> Path:
    """某会话的输出目录：{输出根}/{租户}/{会话ID}"""
    return output_root() / _safe_part(tenant_id, "default") / _safe_part(session_id, "session")


def build_image_path(tenant_id: str, session_id: str, platform: str, category: str,
                     index: int, ext: str = ".png", slot_id: str = "") -> Path:
    """`{输出根}/{租户}/{会话ID}/{平台}_{品类}[_{槽位}]_{序号}{ext}`（index 从 1 开始）

    带槽位（`main_white` 等）是为了让导出的一整套图**一眼能对上平台要求的槽位**，
    导出的 ZIP 就是可直接上传的套图。

    序号**放在最后**是有意为之：`find_session_file()` 按 `_{index}` 结尾匹配，这样加槽位
    不会破坏现有的按序号下载/查找逻辑。
    """
    parts = [_safe_part(platform, "na"), _safe_part(category, "未分类")]
    slot = _safe_part(slot_id, "") if slot_id else ""
    if slot:
        parts.append(slot)
    parts.append(str(int(index)))
    return session_dir(tenant_id, session_id) / f"{'_'.join(parts)}{ext}"


def _decode_data_uri(url: str) -> bytes:
    """解析 `data:image/png;base64,xxxx` → bytes（失败返回 b""）"""
    try:
        header, _, payload = url.partition(",")
        if "base64" not in header:
            return b""
        return base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        return b""


def _decode_inline(b64: str) -> bytes:
    try:
        return base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError):
        return b""


async def _fetch_remote(url: str) -> bytes:
    """下载远程图（真实 Provider 返回 URL 而非内联 base64）；失败返回 b"" """
    if not url.lower().startswith(("http://", "https://")):
        return b""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
            _export_logger.warning("导出：远程图下载失败 HTTP %s (%s)", resp.status_code, url[:120])
    except Exception as e:  # noqa: BLE001 — 落盘是尽力而为
        _export_logger.warning("导出：远程图下载异常 %s (%s)", e, url[:120])
    return b""


async def _image_bytes(image: dict) -> bytes:
    """从 image 记录取出原始字节：内联 base64 → data URI → 远程 URL"""
    inline = str(image.get("base64_data") or "")
    if inline:
        data = _decode_inline(inline)
        if data:
            return data
    url = str(image.get("image_url") or "")
    if url.startswith("data:"):
        return _decode_data_uri(url)
    return await _fetch_remote(url)


# 公开别名：质量体检（harness/image_quality.py）与其它模块也要按同样的规则取字节
image_bytes = _image_bytes


def _write_bytes(path: Path, data: bytes) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)          # 原子替换（与 checkpoint 同一套写法）
    return len(data)


async def save_images(session_id: str, tenant_id: str, platform: str, category: str,
                      images: list) -> list[dict]:
    """把一组生成图落盘，返回每张的 {index, path, rel_path, bytes, ok, error}

    尽力而为：单张失败不影响其余，也不抛异常（调用方是生成主流程）。
    """
    results: list[dict] = []
    for i, image in enumerate(images or [], start=1):
        info = {"index": i, "path": "", "rel_path": "", "bytes": 0, "ok": False, "error": ""}
        try:
            if not isinstance(image, dict):
                info["error"] = "非法图像记录"
                results.append(info)
                continue
            data = await _image_bytes(image)
            if not data:
                info["error"] = "无可用图像数据（base64/URL 均为空）"
                results.append(info)
                continue
            path = build_image_path(tenant_id, session_id, platform, category, i,
                                    detect_extension(data), slot_id=str(image.get("slot_id") or ""))
            info["bytes"] = await asyncio.to_thread(_write_bytes, path, data)
            info["path"] = str(path)
            try:
                # 对外统一 POSIX 风格（跨平台展示一致；绝对路径另由设置页给出）
                info["rel_path"] = path.relative_to(output_root()).as_posix()
            except ValueError:
                info["rel_path"] = path.as_posix()
            info["ok"] = True
        except Exception as e:  # noqa: BLE001 — 落盘失败只告警
            info["error"] = f"{type(e).__name__}: {e}"
            _export_logger.warning("导出：图片落盘失败 (session=%s #%s): %s",
                                   session_id, i, info["error"])
        results.append(info)
    return results


def list_session_files(tenant_id: str, session_id: str) -> list[Path]:
    """某会话已落盘的图片文件（按文件名排序，不存在返回空列表）"""
    directory = session_dir(tenant_id, session_id)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and not p.name.endswith(".part"))


# ── 上传原图落盘（inputs/）──
#
# 为什么需要：参考图（"文+图"里的"图"）此前只活在内存与**完整** checkpoint 里，而轻量快照
# 会剔除 `task.product_images`（base64 太占空间）→ 崩溃恢复或重启后参考图直接消失，
# i2i 会**静默退化成纯文生图**，又回到"模型编造包装文字"的老问题。
# 落盘后：参考图从磁盘解析（几十 KB 级），会话恢复、重启、审查比对都还在。

INPUT_DIR_NAME = "inputs"


def input_dir(tenant_id: str, session_id: str) -> Path:
    return session_dir(tenant_id, session_id) / INPUT_DIR_NAME


def build_input_path(tenant_id: str, session_id: str, index: int, ext: str = ".png") -> Path:
    """`{会话目录}/inputs/upload_{序号}{ext}`（序号即提示词里"图一/图二"的顺序）"""
    return input_dir(tenant_id, session_id) / f"upload_{int(index)}{ext}"


def _decode_source(source) -> bytes:
    """上传图（裸 base64 / data URI）→ 字节"""
    text = str(source or "").strip()
    if not text:
        return b""
    if text.startswith("data:"):
        return _decode_data_uri(text)
    return _decode_inline(text)


async def save_inputs(tenant_id: str, session_id: str, images) -> list[dict]:
    """把用户上传的原图落盘到 `inputs/`，返回每张的元数据（尽力而为，绝不抛异常）

    元数据会写进 `task.input_files`，前端据此展示"图一/图二"，参考图解析也能直接读盘。
    """
    results: list[dict] = []
    for index, item in enumerate(images or [], start=1):
        source = item.get("base64_data") if isinstance(item, dict) else item
        info = {"index": index, "slot": f"upload_{index}", "rel_path": "", "bytes": 0,
                "mime": "", "ok": False, "error": ""}
        try:
            data = _decode_source(source)
            if not data:
                info["error"] = "无可用图像数据"
                results.append(info)
                continue
            path = build_input_path(tenant_id, session_id, index, detect_extension(data))
            info["bytes"] = await asyncio.to_thread(_write_bytes, path, data)
            info["mime"] = sniff_mime(data)
            info["rel_path"] = path.relative_to(output_root()).as_posix()
            info["ok"] = True
        except Exception as exc:  # noqa: BLE001 — 落盘失败不影响会话创建
            info["error"] = f"{type(exc).__name__}: {exc}"
            _export_logger.warning("上传图落盘失败 (session=%s #%s): %s",
                                   session_id, index, info["error"])
        results.append(info)
    return results


def load_inputs(tenant_id: str, session_id: str) -> list[dict]:
    """读取已落盘的上传原图（按序号排序；不存在返回空列表）"""
    directory = input_dir(tenant_id, session_id)
    if not directory.is_dir():
        return []
    items: list[dict] = []
    for path in sorted(p for p in directory.iterdir()
                       if p.is_file() and not p.name.endswith(".part")):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        items.append({"slot": path.stem, "rel_path": path.relative_to(output_root()).as_posix(),
                      "bytes": len(data), "mime": sniff_mime(data), "data": data})
    return items


def read_output_file(rel_path: str) -> bytes | None:
    """按相对路径读取输出根内的文件（越界返回 None）"""
    text = str(rel_path or "").strip()
    if not text:
        return None
    try:
        root = output_root().resolve()
        candidate = (root / text).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate != root and root not in candidate.parents:
        return None
    try:
        return candidate.read_bytes() if candidate.is_file() else None
    except OSError:
        return None


def find_session_file(tenant_id: str, session_id: str, index: int) -> Path | None:
    """按序号找已落盘文件（文件名以 `_{index}.ext` 结尾；找不到返回 None）"""
    suffix = f"_{int(index)}"
    for path in list_session_files(tenant_id, session_id):
        if path.stem.endswith(suffix):
            return path
    return None


def build_session_zip(tenant_id: str, session_id: str) -> bytes | None:
    """把某会话的输出目录打成 ZIP（无文件返回 None）"""
    files = list_session_files(tenant_id, session_id)
    if not files:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
    return buf.getvalue()
