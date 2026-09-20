"""用户风格词条存储 —— 导入的照片 → 文字档案（**照片永不进生图链路**）

## 它存什么

```
data/style_library/entries.json      # 用户词条（原子替换 + 模块级写锁）
data/style_library/stats.json        # 采纳/用量计数（best-effort）
data/style_library/<id>/photo_N.jpg  # 预处理后的照片（≤MAX_PHOTOS 张，仅供<重新分析>与封面）
data/style_library/<id>/thumb_N.jpg  # 320px 缩略图（列表用；接口以 data URI 返回，避免鉴权头问题）
```

## 安全边界（**本模块的存在意义就是这个边界**）

用户导入的照片很可能是**别人家的包装**。图片直进生图链路会把别人包装上的文字/图案带进本商品
（本仓库两次事故：记忆参考把"水飞蓟"抄进成分未确认的商品、`DEFOEBUENA®` 被编成 `NUTRIVA®`），
还会挤占实拍参考额度（默认 4 张全给商品图）。所以：

- 照片只落在**本模块自己的目录**，**绝不写 `data/inputs/`**（会话上传目录），
  也**不 import** `harness/reference_images.py` / `vision_payload.py`（有静态测试钉住）；
- 照片只有两个用途：给「风格档案员」做**一次**风格分析、给界面显示缩略图；
- 分析结果经 `sanitize_entry()` 剔除品牌/成分/认证/色值后才入库（剔除项如实展示）。

## 为什么读要缓存、写要加锁

- **读**：`select_by_slot()` 会在每次提示词生成/审核时读一遍用户词条，用它做"逐槽位注入"。
  照 `core/platforms.py` 的教训，按 `(路径, mtime_ns, size)` 做 mtime 缓存 —— 配置改了立刻生效，
  但不会每次都重新解析。
- **写**：create/update/delete 是"读-改-写"整文件，必须持**模块级锁**（跨实例共享，
  与 `audit_logger._WRITE_LOCK` 同一修法：实例属性锁在多实例下等于没锁），
  并在临时文件写完后 `os.replace` 原子替换（避免半写把用户词条写坏）。
"""

import base64
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.harness.style_library import (
    normalize_anchor,
    sanitize_entry,
    validate_entry,
)

# 模块级写锁（跨实例共享；见模块 docstring）
_WRITE_LOCK = threading.Lock()

ENTRIES_FILE = "entries.json"
STATS_FILE = "stats.json"
# 内置档案的"停用"状态：**不能写回 config/style_library.yaml**
# （程序写配置会把注释（=文档）全清掉，本仓库已有教训）→ 单独存一份 id 列表
BUILTIN_DISABLED_FILE = "builtin_disabled.json"

MAX_ENTRIES_PER_TENANT = 50          # 单租户词条上限（防止无界增长）
# 单条最多几张照片（用户 2026-09-19 质疑"为什么限定只能输入 6 张？"）：
#   - 「6」此前是同一个数字在四处各写一遍（本模块/接口/档案员/前端），**没有供应商或成本依据**
#     （用户实测：6 张 1 次调用 $0.000932，落盘 617KB）；真正的边界是**单次请求的体积与上下文**；
#   - 20 覆盖"含详情页的整套"（拼多多 10／Amazon 9／淘宝 8）加余量；与 `api.MAX_UPLOAD_IMAGES`
#     不是一回事（那是**本次上传的本商品图**，这是**参考套图**）；
#   - 超过单批（`VISION_BATCH_SIZE`）时由「风格档案员」**分批**分析（分批 = 多次调用 = 多次计费）。
MAX_PHOTOS = 20
VISION_BATCH_SIZE = 12               # 单次视觉调用的张数上限（档案员与收割阈值共用）
MAX_PHOTO_BYTES = 20 * 1024 * 1024   # 单张上限（与会话上传同一口径）
THUMB_MAX_SIDE = 320
ANALYZING_TIMEOUT_S = 600            # 超过它就认为分析中断（进程重启/任务丢失）
ANALYZING_TIMEOUT_PER_BATCH_S = 240  # 每多一批放宽（分批 = 多次调用，理所当然更久）

# 读取缓存：(路径, mtime_ns, size) → 解析结果
_CACHE: dict[str, tuple[tuple, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def analyzing_timeout_s(photo_count: int) -> int:
    """"卡在 analyzing"的收割阈值（按**批数**放宽）

    分批 = 多次视觉调用（每批还可能重试一次），固定 600s 会在 20 张时误判"分析中断"，
    于是界面先显示失败、随后 `apply_analysis` 又把状态翻成 ready（用户看到的是一次灵异事件）。
    """
    try:
        count = max(0, int(photo_count or 0))
    except (TypeError, ValueError):
        count = 0
    batches = max(1, (count + VISION_BATCH_SIZE - 1) // VISION_BATCH_SIZE)
    return ANALYZING_TIMEOUT_S + (batches - 1) * ANALYZING_TIMEOUT_PER_BATCH_S


def _entry_id() -> str:
    return f"st_{uuid.uuid4().hex[:12]}"


def _read_json(path: Path) -> Any:
    """读 JSON；文件不存在/损坏都返回空（损坏时**改名留证**，不静默丢数据）"""
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        try:
            broken = path.with_suffix(path.suffix + ".broken")
            path.replace(broken)
        except OSError:
            pass
        return None


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class StyleStore:
    """用户风格词条存储（纯本地文件；所有写操作持模块级锁）"""

    def __init__(self, storage_dir: str = ""):
        self._explicit_dir = Path(storage_dir) if storage_dir else None

    # ── 路径 ──

    @property
    def _dir(self) -> Path:
        """词条目录：显式注入优先；否则 `data_root()/style_library`

        **必须在使用时调用 `data_root()`**（`config.py` 的明确要求）：模块级固化会在
        import 期就锁死真实路径，测试（`ECOMM_DATA_DIR` 指向 tmp）就会往真实 `data/` 里灌数据。
        """
        if self._explicit_dir is not None:
            return self._explicit_dir
        from src.core.config import data_root
        return data_root() / "style_library"

    @property
    def entries_path(self) -> Path:
        return self._dir / ENTRIES_FILE

    @property
    def stats_path(self) -> Path:
        return self._dir / STATS_FILE

    def photo_dir(self, entry_id: str) -> Path:
        return self._dir / str(entry_id)

    # ── 读 ──

    def _all_entries(self) -> list[dict]:
        path = self.entries_path
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return []
        cached = _CACHE.get("entries")
        if cached and cached[0] == key:
            return cached[1]
        raw = _read_json(path)
        items = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        _CACHE["entries"] = (key, items)
        return items

    def list_entries(self, tenant_id: str | None = None) -> list[dict]:
        """词条列表（按创建时间倒序）；`tenant_id=None` = 全部租户"""
        items = self._reap_interrupted(self._all_entries())
        if tenant_id is not None:
            items = [item for item in items if str(item.get("tenant_id") or "") == str(tenant_id)]
        return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def get_entry(self, entry_id: str, tenant_id: str | None = None) -> dict | None:
        for item in self.list_entries(tenant_id):
            if str(item.get("id")) == str(entry_id):
                return item
        return None

    def count(self, tenant_id: str) -> int:
        return len(self.list_entries(tenant_id))

    def _reap_interrupted(self, items: list[dict]) -> list[dict]:
        """把"卡在 analyzing 太久"的词条标成 failed（进程重启/任务丢失后不会永远转圈）

        惰性收割：只在列表/读取时判定，不引入后台线程。阈值按**照片数**确定（分批更久）。
        """
        changed = False
        for item in items:
            if item.get("status") != "analyzing":
                continue
            stamp = str(item.get("analyzing_at") or item.get("updated_at") or "")
            try:
                started = datetime.fromisoformat(stamp)
            except ValueError:
                started = None
            if started is None:
                continue
            age = (datetime.now(timezone.utc) - started).total_seconds()
            if age > analyzing_timeout_s(len(item.get("photos") or [])):
                item["status"] = "failed"
                item["error"] = "分析中断（进程重启或任务丢失），可点「重试」重新分析"
                item["updated_at"] = _now()
                changed = True
        if changed:
            self._save_entries(items)
        return items

    # ── 写 ──

    def _save_entries(self, items: list[dict]) -> None:
        with _WRITE_LOCK:
            _write_json_atomic(self.entries_path, items)

    def create_entry(self, *, tenant_id: str, name: str, applies_to: dict | None = None,
                     as_anchor: bool = True, photos: list[bytes] | None = None) -> dict:
        """建一条词条（状态 `analyzing`）；照片落独立目录 + 生成缩略图

        Raises:
            ValueError: 名称为空 / 没有有效照片 / 张数超限 / 超出词条上限
        """
        title = str(name or "").strip()
        if not title:
            raise ValueError("请先给这个风格起个名字")
        payloads = [data for data in (photos or []) if data]
        if not payloads:
            raise ValueError("请至少导入 1 张照片")
        if len(payloads) > MAX_PHOTOS:
            raise ValueError(f"最多 {MAX_PHOTOS} 张照片（超出会分批多次调用；"
                             f"单条词条上限就是 {MAX_PHOTOS} 张）")
        too_big = [index for index, data in enumerate(payloads, start=1)
                   if len(data) > MAX_PHOTO_BYTES]
        if too_big:
            raise ValueError(f"第 {'、'.join(str(i) for i in too_big)} 张超过 "
                             f"{MAX_PHOTO_BYTES // 1024 // 1024}MB")

        with _WRITE_LOCK:
            items = self._all_entries()
            mine = [item for item in items
                    if str(item.get("tenant_id") or "") == str(tenant_id)]
            if len(mine) >= MAX_ENTRIES_PER_TENANT:
                raise ValueError(f"每个租户最多 {MAX_ENTRIES_PER_TENANT} 条风格词条，请先删除不用的")

        entry_id = _entry_id()
        directory = self.photo_dir(entry_id)
        directory.mkdir(parents=True, exist_ok=True)
        stored: list[dict] = []
        for index, data in enumerate(payloads, start=1):
            name_on_disk = f"photo_{index}.jpg"
            (directory / name_on_disk).write_bytes(data)
            thumb_name = self._write_thumb(directory, index, data)
            stored.append({"file": name_on_disk, "thumb": thumb_name, "bytes": len(data)})

        entry = {
            "id": entry_id, "tenant_id": str(tenant_id or "default"), "source": "用户导入",
            "name": title, "name_suggestions": [], "summary": "", "style_words": "",
            "applies_to": applies_to if isinstance(applies_to, dict) else {},
            "background": "", "composition": "", "lighting": "", "materials": "",
            "elements": [], "palette_roles": {}, "whitespace": "", "forbid": [],
            "keep_clear_hint": "", "as_anchor": bool(as_anchor),
            "taste_verdict": "", "reward_points": [], "avoid_points": [],
            "status": "analyzing", "enabled": True, "error": "",
            "photos": stored, "removed": [], "adopted": 0,
            "analysis": {}, "analyzing_at": _now(),
            "created_at": _now(), "updated_at": _now(),
        }
        with _WRITE_LOCK:
            items = self._all_entries()
            items.append(entry)
            _write_json_atomic(self.entries_path, items)
        _CACHE.pop("entries", None)
        return entry

    @staticmethod
    def _write_thumb(directory: Path, index: int, data: bytes) -> str:
        """写 320px 缩略图；Pillow 不可用/图片损坏时返回空串（缩略图是锦上添花，不阻断）"""
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                image = image.convert("RGB")
                image.thumbnail((THUMB_MAX_SIDE, THUMB_MAX_SIDE))
                name = f"thumb_{index}.jpg"
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=70, optimize=True)
            (directory / name).write_bytes(buffer.getvalue())
            return name
        except Exception:  # noqa: BLE001 — 缩略图失败不影响词条
            return ""

    def update_entry(self, entry_id: str, updates: dict, *,
                     tenant_id: str | None = None, allow_all: bool = True) -> dict:
        """更新词条字段；内置词条由调用方用 `allow_all=False` 只放行 enabled

        Returns: 更新后的词条（副本）
        """
        if not isinstance(updates, dict):
            raise ValueError("更新内容必须是对象")
        with _WRITE_LOCK:
            items = self._all_entries()
            target = None
            for item in items:
                if str(item.get("id")) != str(entry_id):
                    continue
                if tenant_id is not None and str(item.get("tenant_id") or "") != str(tenant_id):
                    raise ValueError("词条不存在")
                target = item
                break
            if target is None:
                raise ValueError("词条不存在")
            allowed = set(updates) if allow_all else (set(updates) & {"enabled"})
            if not allow_all and set(updates) - allowed:
                raise ValueError("内置档案只能启停，不能编辑内容")
            target.update({key: value for key, value in updates.items() if key in allowed})
            target["updated_at"] = _now()
            _write_json_atomic(self.entries_path, items)
        _CACHE.pop("entries", None)
        return dict(target)

    # ── 启用/停用（**同租户同一时刻只有一套生效**：一轮会话一套风格词）──

    def set_entry_enabled(self, entry_id: str, enabled: bool, *,
                          tenant_id: str | None = None) -> dict:
        """启用/停用一套风格词；启用时**自动停用同租户的其他套**

        用户 2026-09-20 原话："应该是一组生成图用一种风格，或者说是一轮会话里只用一个
        风格词"，并问"是否有在风格词库那里限定启用一个风格另一个风格自动停用" ——
        此前没有：实测每张图会被两套档案同时指挥（你的风格 + 一套内置原型，且常互相矛盾）。
        同日口径更正：单位是**一套**风格词（一条记录携带的那组字段），不是"一个词"。

        启用一套 = 选它作为"我现在的套图风格"，所以其他用户词条必须一起停用。
        **内置档案不参与**（它们是逐槽位的通用原型，不是"一整套风格"）。

        Returns: `{"entry": {...}, "auto_disabled": [{"id", "name"}]}`
        """
        with _WRITE_LOCK:
            items = self._all_entries()
            target = None
            for item in items:
                if str(item.get("id")) != str(entry_id):
                    continue
                if tenant_id is not None and str(item.get("tenant_id") or "") != str(tenant_id):
                    raise ValueError("词条不存在")
                target = item
                break
            if target is None:
                raise ValueError("词条不存在")
            target["enabled"] = bool(enabled)
            target["updated_at"] = _now()
            auto_disabled: list[dict] = []
            if enabled:
                auto_disabled = self._disable_others_locked(
                    items, entry_id, tenant_id=str(target.get("tenant_id") or ""))
            _write_json_atomic(self.entries_path, items)
        _CACHE.pop("entries", None)
        return {"entry": dict(target), "auto_disabled": auto_disabled}

    @staticmethod
    def _disable_others_locked(items: list[dict], entry_id: str, *,
                               tenant_id: str) -> list[dict]:
        """（**调用方必须已持写锁**）停用同租户其他仍启用的词条，返回被停用名单"""
        disabled: list[dict] = []
        for item in items:
            if str(item.get("id")) == str(entry_id):
                continue
            if str(item.get("tenant_id") or "") != str(tenant_id):
                continue
            if not item.get("enabled", True):
                continue
            item["enabled"] = False
            item["updated_at"] = _now()
            disabled.append({"id": item.get("id"), "name": item.get("name")})
        return disabled

    def disable_other_entries(self, entry_id: str, *, tenant_id: str | None = None) -> list[dict]:
        """停用同租户其他已启用的词条（分析成功后调用）；返回被停用名单"""
        with _WRITE_LOCK:
            items = self._all_entries()
            target = next((item for item in items
                           if str(item.get("id")) == str(entry_id)), None)
            if target is None:
                raise ValueError("词条不存在")
            owner = str(tenant_id if tenant_id is not None
                        else (target.get("tenant_id") or ""))
            disabled = self._disable_others_locked(items, entry_id, tenant_id=owner)
            if disabled:
                _write_json_atomic(self.entries_path, items)
        if disabled:
            _CACHE.pop("entries", None)
        return disabled

    def apply_analysis(self, entry_id: str, payload: dict, *, usage: dict | None = None) -> dict:
        """把「风格档案员」的分析结果写进词条（**先清洗再入库**）

        必填字段被清洗空 → 状态 `failed` + 可读原因（绝不静默产出一条空档案）。
        """
        cleaned, removed = sanitize_entry(payload)
        # 分析结果里通常没有 name（名字是用户在弹窗里起的）：**不能**用空串覆盖掉它
        # （实测踩到：每次分析都会把用户起的名字清空，词条随后因"缺少 name"被检索丢弃）
        if not cleaned.get("name"):
            cleaned.pop("name", None)
        # 分析成功 → 这套就是"我现在的套图风格"，同租户其他套自动停用（一轮会话一套风格词）。
        # 若用户曾**手工停用**过它，则尊重他的手势、不在此刻强行启用（也不动别人）。
        auto_disabled: list[dict] = []
        try:
            target = self.get_entry(entry_id)
            if target and bool(target.get("enabled", True)):
                auto_disabled = self.disable_other_entries(
                    entry_id, tenant_id=str(target.get("tenant_id") or ""))
        except Exception:  # noqa: BLE001 — 切换失败不能挡住落库（检索侧还有一层"只用一套"）
            auto_disabled = []
        # `removed_brand_text` 与回落说明是**分析的元信息**（不是设计字段）：
        # `sanitize_entry` 的白名单会丢掉它们，所以要显式保存到 `analysis` 里 ——
        # 界面上"已剔除 N 处品牌文字"就是读这里（不静默）。
        analysis_meta = {
            "usage": usage or {}, "at": _now(),
            "removed_brand_text": [str(item) for item in (payload.get("removed_brand_text") or [])][:8],
            "fallback_note": str(payload.get("fallback_note") or ""),
        }
        if auto_disabled:
            analysis_meta["auto_disabled"] = auto_disabled
        if not cleaned.get("background") and not cleaned.get("composition") \
                and not cleaned.get("lighting") and not cleaned.get("materials"):
            return self.set_status(
                entry_id, "failed",
                error="分析结果没有可用的设计要点（可能照片里没有可识别的商品/版式），可点「重试」",
                removed=removed, analysis=analysis_meta)
        fields = dict(cleaned)
        fields.update({
            "status": "ready", "error": "", "removed": removed,
            "analysis": analysis_meta,
        })
        if not fields.get("style_words"):
            fields["style_words"] = fields.get("summary", "")
        return self.update_entry(entry_id, fields, allow_all=True)

    def set_status(self, entry_id: str, status: str, **fields) -> dict:
        payload = {"status": status, **fields}
        if status == "analyzing":
            payload["analyzing_at"] = _now()
        return self.update_entry(entry_id, payload, allow_all=True)

    def library_items(self, tenant_id: str | None = None
                      ) -> tuple[list[dict], list[dict], list[dict]]:
        """给 `style_library.load_library()` 用的合并项：`(词条, 锚点, 被丢弃的)`

        只把 **ready + enabled** 的词条交给检索；未就绪/停用的如实记账（不静默消失）。
        """
        entries: list[dict] = []
        anchors: list[dict] = []
        dropped: list[dict] = []
        for item in self.list_entries(tenant_id):
            entry_id = str(item.get("id") or "")
            status = str(item.get("status") or "")
            if status != "ready":
                dropped.append({"id": entry_id, "reasons": [
                    f"词条「{item.get('name') or entry_id}」尚未就绪（{status or 'draft'}）"]})
                continue
            if not item.get("enabled", True):
                dropped.append({"id": entry_id, "reasons": [
                    f"词条「{item.get('name') or entry_id}」已停用"]})
                continue
            candidate = dict(item)
            candidate["source"] = "用户导入"
            reasons = validate_entry({**candidate, "name": candidate.get("name") or entry_id})
            if reasons:
                dropped.append({"id": entry_id, "reasons": reasons})
                continue
            entries.append(candidate)
            if candidate.get("as_anchor"):
                anchor = normalize_anchor({
                    "id": f"anchor_{entry_id}",
                    "name": candidate.get("name") or entry_id,
                    "source": "用户导入",
                    "taste_verdict": candidate.get("taste_verdict")
                    or candidate.get("style_words") or candidate.get("summary"),
                    "reward_points": candidate.get("reward_points") or [],
                    "avoid_points": candidate.get("avoid_points") or [],
                    "applies_to": candidate.get("applies_to") or {},
                    "enabled": True,
                })
                if anchor:
                    anchors.append(anchor)
        return entries, anchors, dropped

    def delete_entry(self, entry_id: str, *, tenant_id: str | None = None) -> dict:
        with _WRITE_LOCK:
            items = self._all_entries()
            kept: list[dict] = []
            removed: dict | None = None
            for item in items:
                if str(item.get("id")) == str(entry_id) and removed is None:
                    if tenant_id is not None \
                            and str(item.get("tenant_id") or "") != str(tenant_id):
                        raise ValueError("词条不存在")
                    removed = item
                    continue
                kept.append(item)
            if removed is None:
                raise ValueError("词条不存在")
            _write_json_atomic(self.entries_path, kept)
        _CACHE.pop("entries", None)
        # 照片目录一并清理（失败不影响删除结果）
        directory = self.photo_dir(entry_id)
        try:
            if directory.is_dir():
                for child in directory.iterdir():
                    child.unlink(missing_ok=True)
                directory.rmdir()
        except OSError:
            pass
        return removed

    # ── 照片增删（用户 2026-09-19："我给的是一套图片" —— 一套图要能补齐/去掉几张）──

    def add_photos(self, entry_id: str, payloads: list[bytes], *,
                   tenant_id: str | None = None) -> dict:
        """**追加**照片（只 append → 已有序号不变，逐张角色仍然对得上）

        Raises: ValueError（词条不存在 / 没照片 / 超上限 / 单张过大）
        """
        photos = [data for data in (payloads or []) if data]
        if not photos:
            raise ValueError("没有可追加的照片")
        with _WRITE_LOCK:
            items = self._all_entries()
            target = None
            for item in items:
                if str(item.get("id")) != str(entry_id):
                    continue
                if tenant_id is not None and str(item.get("tenant_id") or "") != str(tenant_id):
                    raise ValueError("词条不存在")
                target = item
                break
            if target is None:
                raise ValueError("词条不存在")
            existing = list(target.get("photos") or [])
            if len(existing) + len(photos) > MAX_PHOTOS:
                raise ValueError(f"最多 {MAX_PHOTOS} 张照片"
                                 f"（当前 {len(existing)} 张，还能加 {MAX_PHOTOS - len(existing)} 张）")
            too_big = [index for index, data in enumerate(photos, start=1)
                       if len(data) > MAX_PHOTO_BYTES]
            if too_big:
                raise ValueError(f"第 {'、'.join(str(i) for i in too_big)} 张超过 "
                                 f"{MAX_PHOTO_BYTES // 1024 // 1024}MB")
            directory = self.photo_dir(entry_id)
            directory.mkdir(parents=True, exist_ok=True)
            stored = list(existing)
            for offset, data in enumerate(photos, start=1):
                index = len(stored) + 1
                name_on_disk = f"photo_{index}.jpg"
                (directory / name_on_disk).write_bytes(data)
                stored.append({"file": name_on_disk,
                               "thumb": self._write_thumb(directory, index, data),
                               "bytes": len(data)})
            target["photos"] = stored
            target["updated_at"] = _now()
            _write_json_atomic(self.entries_path, items)
        _CACHE.pop("entries", None)
        return dict(target)

    def remove_photo(self, entry_id: str, index: int, *,
                     tenant_id: str | None = None) -> dict:
        """移除第 `index` 张（1 起）并**重排**剩余文件

        重排会让序号变化 → 一并清空 `shot_roles`（共同美术保留），并如实回报
        `cleared_roles`。否则"参考第2张"会指向另一张照片 —— 宁可让用户再点一次「再分析」。
        """
        try:
            position = int(index)
        except (TypeError, ValueError):
            raise ValueError("照片序号必须是整数")
        with _WRITE_LOCK:
            items = self._all_entries()
            target = None
            for item in items:
                if str(item.get("id")) != str(entry_id):
                    continue
                if tenant_id is not None and str(item.get("tenant_id") or "") != str(tenant_id):
                    raise ValueError("词条不存在")
                target = item
                break
            if target is None:
                raise ValueError("词条不存在")
            photos = list(target.get("photos") or [])
            if not 1 <= position <= len(photos):
                raise ValueError(f"照片序号超出范围（1–{len(photos)}）")
            if len(photos) <= 1:
                raise ValueError("至少保留 1 张照片；整条不要了就删除词条")

            directory = self.photo_dir(entry_id)
            remaining = [item for item_index, item in enumerate(photos, start=1)
                         if item_index != position]
            for item_index, item in enumerate(remaining, start=1):
                old_file = str(item.get("file") or "")
                old_thumb = str(item.get("thumb") or "")
                new_file = f"photo_{item_index}.jpg"
                if old_file and old_file != new_file:
                    try:
                        (directory / old_file).replace(directory / new_file)
                    except OSError:
                        pass
                if old_thumb:
                    new_thumb = f"thumb_{item_index}.jpg"
                    if old_thumb != new_thumb:
                        try:
                            (directory / old_thumb).replace(directory / new_thumb)
                        except OSError:
                            pass
                    item["thumb"] = new_thumb
                item["file"] = new_file
            # 清掉可能残留的最后一张文件（重排后目录里只应留下 remaining 张）
            for stale in directory.glob("photo_*.jpg"):
                if stale.name not in {str(item.get("file")) for item in remaining}:
                    stale.unlink(missing_ok=True)
            for stale in directory.glob("thumb_*.jpg"):
                if stale.name not in {str(item.get("thumb")) for item in remaining}:
                    stale.unlink(missing_ok=True)

            cleared = bool(target.get("shot_roles"))
            target["photos"] = remaining
            if cleared:
                target["shot_roles"] = []
                target["shot_flow"] = target.get("shot_flow") or ""
            target["updated_at"] = _now()
            _write_json_atomic(self.entries_path, items)
        _CACHE.pop("entries", None)
        return {"entry": dict(target), "cleared_roles": cleared}

    # ── 采纳计数（best-effort）──

    def adopt(self, entry_ids, *, count: int = 1) -> None:
        """记录"这套图用了哪几条词条"（卡片上的"被 N 次会话采用"）

        **绝不影响出图**：任何异常都吞掉。计数是参考值，不是账本。
        """
        ids = [str(item) for item in (entry_ids or []) if str(item or "").strip()]
        if not ids:
            return
        try:
            stats = _read_json(self.stats_path)
            stats = stats if isinstance(stats, dict) else {}
            adopted = stats.get("adopted") if isinstance(stats.get("adopted"), dict) else {}
            for entry_id in ids:
                adopted[entry_id] = int(adopted.get(entry_id) or 0) + max(0, int(count))
            stats["adopted"] = adopted
            stats["updated_at"] = _now()
            with _WRITE_LOCK:
                _write_json_atomic(self.stats_path, stats)
        except Exception:  # noqa: BLE001 — 计数失败不能影响出图
            return

    def adopted_counts(self) -> dict[str, int]:
        stats = _read_json(self.stats_path)
        adopted = (stats or {}).get("adopted") if isinstance(stats, dict) else None
        return {str(k): int(v) for k, v in (adopted or {}).items()} if isinstance(adopted, dict) else {}

    # ── 内置档案的启停（配置只读，状态单独存）──

    @property
    def builtin_disabled_path(self) -> Path:
        return self._dir / BUILTIN_DISABLED_FILE

    def disabled_builtin_ids(self) -> set[str]:
        raw = _read_json(self.builtin_disabled_path)
        return {str(item) for item in raw} if isinstance(raw, list) else set()

    def set_builtin_enabled(self, entry_id: str, enabled: bool) -> bool:
        target = str(entry_id or "").strip()
        if not target:
            raise ValueError("缺少词条 id")
        with _WRITE_LOCK:
            ids = self.disabled_builtin_ids()
            if enabled:
                ids.discard(target)
            else:
                ids.add(target)
            _write_json_atomic(self.builtin_disabled_path, sorted(ids))
        return bool(enabled)

    # ── 界面用：缩略图 ──

    def thumb_data_uri(self, entry: dict, index: int = 1) -> str:
        """第 N 张缩略图的 data URI（`<img>` 带不上鉴权头，所以走内联）"""
        photos = entry.get("photos") or []
        if not isinstance(photos, list) or not photos:
            return ""
        target = photos[min(max(index - 1, 0), len(photos) - 1)]
        name = str(target.get("thumb") or target.get("file") or "")
        if not name:
            return ""
        path = self.photo_dir(str(entry.get("id") or "")) / name
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}"

    def photos_data_uri(self, entry: dict) -> list[str]:
        photos = entry.get("photos") or []
        return [uri for uri in (self.thumb_data_uri(entry, index)
                                for index in range(1, len(photos) + 1)) if uri]

    def read_photos(self, entry: dict) -> list[bytes]:
        """原始照片字节（**只给「风格档案员」做分析用**；不要传给生图）"""
        out: list[bytes] = []
        for item in entry.get("photos") or []:
            name = str((item or {}).get("file") or "")
            if not name:
                continue
            try:
                out.append((self.photo_dir(str(entry.get("id"))) / name).read_bytes())
            except OSError:
                continue
        return out


def entry_to_summary(entry: dict, *, store: StyleStore | None = None,
                     adopted: dict | None = None, cost: dict | None = None) -> dict[str, Any]:
    """词条 → 列表用摘要（缩略图走 data URI）"""
    store = store or StyleStore()
    applies = entry.get("applies_to") if isinstance(entry.get("applies_to"), dict) else {}
    usage = (entry.get("analysis") or {}).get("usage") or {}
    counts = adopted if adopted is not None else {}
    auto_disabled = (entry.get("analysis") or {}).get("auto_disabled") or []
    return {
        "id": entry.get("id"), "name": entry.get("name"),
        "source": "builtin" if entry.get("source") == "内置" else "user",
        "status": entry.get("status") or "draft",
        "enabled": bool(entry.get("enabled", True)),
        # 分析成功时"自动停用了哪些词条"（radio）——卡片上如实显示，用户才知道发生了什么
        "auto_disabled": [str(item.get("name") or item.get("id") or "") for item in auto_disabled
                          if isinstance(item, dict)],
        "summary": entry.get("summary") or "", "style_words": entry.get("style_words") or "",
        # 套图结构（卡片上的"套图结构 N 张"chip；完整逐张明细在详情接口里）
        "shot_flow": entry.get("shot_flow") or "",
        "shot_role_count": len([item for item in (entry.get("shot_roles") or [])
                                if isinstance(item, dict)]),
        "as_anchor": bool(entry.get("as_anchor", True)),
        "applies_to": {key: (applies.get(key) or []) for key in
                       ("kinds", "slots", "categories", "platforms")},
        "error": entry.get("error") or "",
        "adopted": int(counts.get(str(entry.get("id")), entry.get("adopted") or 0)),
        "removed": entry.get("removed") or [],
        "usage": {
            "calls": usage.get("calls", 0), "images": usage.get("images", len(entry.get("photos") or [])),
            "elapsed_ms": usage.get("elapsed_ms", 0), "model": usage.get("model", ""),
            "attempts": usage.get("attempts", 0), "maybe_billed": bool(usage.get("maybe_billed")),
            "cost": cost if cost is not None else usage.get("cost"),
        },
        "photo_count": len(entry.get("photos") or []),
        "cover": store.thumb_data_uri(entry, 1),
        "created_at": entry.get("created_at", ""), "updated_at": entry.get("updated_at", ""),
    }
