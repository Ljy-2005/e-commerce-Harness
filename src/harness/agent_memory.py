"""Agent 记忆/学习 — 记录成功模式，召回相似场景的最佳实践"""

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict


class AgentMemory:
    """跨会话的 Agent 经验积累

    记录成功的提示词和分析模式，按品类索引。
    下次遇到相似商品时召回，提高一次通过率。
    """

    def __init__(self, storage_dir: str = ""):
        self._dir = Path(storage_dir) if storage_dir else Path(__file__).parent.parent.parent / "data" / "memory"
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── 记录 ──

    async def remember(
        self,
        session_id: str,
        category: str,
        analysis: dict,
        prompts: dict,
        review: dict,
        compliance: dict | None = None,
    ):
        """记录一次成功的经验"""
        if review.get("overall_score", 0) < 75:
            return  # 低分不记忆

        key = self._category_key(category)
        entry = {
            "session_id": session_id,
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

        # 追加到品类文件
        file = self._dir / f"{key}.jsonl"
        with open(file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── 召回 ──

    async def recall(self, category: str, limit: int = 5) -> list[dict]:
        """召回某品类下历史成功的 Top-N 经验（按评分降序）"""
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

        entries.sort(key=lambda e: e.get("score", 0), reverse=True)
        return entries[:limit]

    async def recall_similar(
        self, category: str, features: list[str], limit: int = 3
    ) -> list[dict]:
        """召回与给定特征相似的成功经验（简单的特征交集匹配）"""
        all_entries = await self.recall(category, limit=20)
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

    async def stats(self) -> dict:
        """记忆库统计"""
        total = 0
        by_category = defaultdict(int)
        scores = []

        for f in self._dir.glob("*.jsonl"):
            with open(f, "r", encoding="utf-8") as fp:
                for line in fp:
                    try:
                        e = json.loads(line.strip())
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
