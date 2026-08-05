"""审计日志 — 追加式 JSONL，每次 Agent 调用完整追踪"""

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

# date 参数校验正则（YYYY-MM-DD）
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class AuditLogger:
    """不可篡改的追加式审计日志（JSONL 格式）

    记录每次 Agent 调用: 谁/何时/调了什么 Provider/结果如何/花了多少钱

    使用:
        logger = AuditLogger()
        await logger.log(entry)
        entries = await logger.query(session_id="abc")
    """

    def __init__(self, log_dir: str = ""):
        self._dir = Path(log_dir) if log_dir else Path(__file__).parent.parent.parent / "data" / "audit"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _today_file(self) -> Path:
        return self._dir / f"audit-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"

    async def log(
        self,
        session_id: str,
        agent_name: str,
        provider_name: str,
        model: str,
        action: str,
        duration_ms: float,
        tokens_used: int,
        cost_usd: float,
        status: str,
        error: str = "",
    ):
        """记录一次审计条目"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "agent": agent_name,
            "provider": provider_name,
            "model": model,
            "action": action,
            "duration_ms": round(duration_ms, 2),
            "tokens": tokens_used,
            "cost_usd": round(cost_usd, 6),
            "status": status,
        }
        if error:
            entry["error"] = error[:500]

        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._write_line, entry)

    def _write_line(self, entry: dict):
        """同步写一行 JSON（由 run_in_executor 调用）"""
        with open(self._today_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def query(
        self,
        session_id: str = "",
        agent_name: str = "",
        date: str = "",  # YYYY-MM-DD
    ) -> list[dict]:
        """查询审计日志

        可按 session_id / agent_name / date 筛选
        """
        if date:
            if not _DATE_RE.match(date):
                return []  # 非法日期格式，返回空（不读任意文件）
            files = [self._dir / f"audit-{date}.jsonl"]
        else:
            files = sorted(self._dir.glob("audit-*.jsonl"), reverse=True)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._query_sync, files, session_id, agent_name)

    def _query_sync(self, files: list[Path], session_id: str, agent_name: str) -> list[dict]:
        """同步查询（由 run_in_executor 调用）"""
        entries = []
        for path in files:
            if not path.exists():
                continue
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if session_id and entry.get("session_id") != session_id:
                        continue
                    if agent_name and entry.get("agent") != agent_name:
                        continue
                    entries.append(entry)
        return entries

    async def stats(self, date: str = "") -> dict:
        """统计汇总"""
        entries = await self.query(date=date)
        total_cost = sum(e.get("cost_usd", 0) for e in entries)
        total_tokens = sum(e.get("tokens", 0) for e in entries)
        by_agent = {}
        for e in entries:
            agent = e.get("agent", "unknown")
            by_agent[agent] = by_agent.get(agent, 0) + 1

        return {
            "date": date or "all",
            "total_calls": len(entries),
            "total_cost_usd": round(total_cost, 6),
            "total_tokens": total_tokens,
            "by_agent": by_agent,
        }
