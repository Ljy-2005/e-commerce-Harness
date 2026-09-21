"""Agent 记忆/学习 — 记录成功模式，召回相似场景的最佳实践"""

import asyncio
import json
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# 模块级写锁（第三轮审计 B1-6）：追加是"打开-写-关闭"，无锁时并发 remember()
# 互相覆盖缓冲区，实测 400 并发只落盘 376 行（丢 24）。与 audit_logger 的
# `_WRITE_LOCK` 同一修法：锁必须跨实例共享，不能用实例属性。
_MEMORY_WRITE_LOCK = threading.Lock()


class AgentMemory:
    """跨会话的 Agent 经验积累

    记录成功的提示词和分析模式，按品类索引。
    下次遇到相似商品时召回，提高一次通过率。

    审计修复：所有文件 IO 经 asyncio.to_thread 转线程，避免阻塞事件循环
    （此前 async def 直接 open/read/write）。
    """

    def __init__(self, storage_dir: str = ""):
        self._explicit_dir = Path(storage_dir) if storage_dir else None
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def _dir(self) -> Path:
        """记忆目录：显式注入优先；否则 data_root()/memory（B2-18：可被 ECOMM_DATA_DIR 重定向）"""
        from src.core.config import data_root
        return self._explicit_dir or (data_root() / "memory")

    # ── 记录 ──

    async def remember(
        self,
        session_id: str,
        category: str,
        analysis: dict,
        prompts: dict,
        review: dict,
        compliance: dict | None = None,
        tenant_id: str = "",
    ):
        """记录一次成功的经验（tenant_id 为租户隔离字段，审计修复）"""
        if review.get("overall_score", 0) < 75:
            return  # 低分不记忆

        key = self._category_key(category)
        entry = {
            "session_id": session_id,
            "tenant_id": tenant_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "features": analysis.get("features", [])[:5],
            "ingredients": analysis.get("ingredients", [])[:5],
            "target_audience": analysis.get("target_audience", {}),
            "style_constraints": analysis.get("style_constraints", {}),
            "prompts": {
                "main": prompts.get("main_image", {}).get("prompt", "")[:300],
                "scene_count": len(prompts.get("scene_images", [])),
            },
            "score": review.get("overall_score", 0),
            "verdict": review.get("verdict", ""),
            "top_praises": review.get("top_praises", [])[:3],
        }

        # 追加到品类文件（线程池中执行）
        await asyncio.to_thread(self._append_entry, key, entry)

    def _append_entry(self, key: str, entry: dict):
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        # B1-6：先序列化再持锁写（锁内只有一次 write），并发下既不丢条目也不产生半行
        with _MEMORY_WRITE_LOCK:
            file = self._dir / f"{key}.jsonl"
            with open(file, "a", encoding="utf-8") as f:
                f.write(line)

    # ── 召回 ──

    async def recall(self, category: str, limit: int = 5, tenant_id: str = "") -> list[dict]:
        """召回某品类下历史成功的 Top-N 经验（按评分降序；tenant_id 非空时过滤，审计修复）"""
        entries = await asyncio.to_thread(self._read_entries, category)
        if tenant_id:
            entries = [e for e in entries if e.get("tenant_id", "") == tenant_id]
        entries.sort(key=lambda e: e.get("score", 0), reverse=True)
        return entries[:limit]

    def _read_entries(self, category: str) -> list[dict]:
        key = self._category_key(category)
        file = self._dir / f"{key}.jsonl"
        if not file.exists():
            return []
        entries = []
        with open(file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
        return entries

    async def recall_similar(
        self, category: str, features: list[str], limit: int = 3, tenant_id: str = ""
    ) -> list[dict]:
        """召回与给定特征相似的成功经验（特征交集匹配；租户过滤，审计修复）"""
        all_entries = await self.recall(category, limit=20, tenant_id=tenant_id)
        if not features:
            return all_entries[:limit]

        scored = []
        for e in all_entries:
            ef = set(e.get("features", []))
            sf = set(features)
            overlap = len(ef & sf)
            scored.append((overlap, e))

        scored.sort(key=lambda x: (-x[0], -x[1].get("score", 0)))
        return [e for _, e in scored[:limit]]

    # ── 统计 ──

    async def stats(self, tenant_id: str = "") -> dict:
        """记忆库统计（tenant_id 非空时仅统计该租户，审计修复）"""
        return await asyncio.to_thread(self._stats_sync, tenant_id)

    def _stats_sync(self, tenant_id: str = "") -> dict:
        total = 0
        by_category = defaultdict(int)
        scores = []

        for f in self._dir.glob("*.jsonl"):
            with open(f, "r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        e = json.loads(line.strip())
                        if tenant_id and e.get("tenant_id", "") != tenant_id:
                            continue
                        total += 1
                        cat = e.get("category", "unknown")
                        by_category[cat] += 1
                        scores.append(e.get("score", 0))
                    except json.JSONDecodeError:
                        continue

        return {
            "total_entries": total,
            "by_category": dict(by_category),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "best_score": max(scores) if scores else 0,
        }

    async def clear(self, category: str = ""):
        """清除记忆（全清或按品类）"""
        await asyncio.to_thread(self._clear_sync, category)

    def _clear_sync(self, category: str):
        if category:
            file = self._dir / f"{self._category_key(category)}.jsonl"
            if file.exists():
                file.unlink()
        else:
            for f in self._dir.glob("*.jsonl"):
                f.unlink()

    def _category_key(self, category: str) -> str:
        """品类名 → 安全文件名"""
        safe = category.replace("/", "_").replace("\\", "_").strip()
        return safe or "unknown"
