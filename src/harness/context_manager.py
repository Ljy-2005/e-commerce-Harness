"""上下文管理器 — Token 估算 + 60% 阈值压缩 + 窗口过渡"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ── 模型上下文窗口大小（tokens）──
MODEL_LIMITS = {
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-haiku-3-5-20241022": 200_000,
    "deepseek-chat": 128_000,
    "deepseek-v3": 128_000,
    "qwen-max": 32_000,
    "qwen-vl-max": 32_000,
    "qwen-plus": 131_072,
    "qwen-vl-plus": 131_072,
    "mock": 200_000,
    "unknown": 128_000,
}


class WindowAction(str, Enum):
    NONE = "none"               # 无需操作
    COMPACT = "compact"          # 压缩旧消息，保留系统 prompt + 最近 N 条
    NEW_WINDOW = "new_window"    # 开启新窗口，注入旧窗口摘要


@dataclass
class ContextReport:
    """上下文检查报告"""
    action: WindowAction = WindowAction.NONE
    estimated_tokens: int = 0
    model_limit: int = 128_000
    usage_ratio: float = 0.0
    messages_before: int = 0
    messages_after: int = 0
    summary: str = ""


class ContextManager:
    """管理每个会话的上下文窗口

    三层防护:
      Layer 1: Token 估算（4 字符 ≈ 1 token 混合编码）
      Layer 2: 60% → 自动压缩旧消息
      Layer 3: 80% → 开启新窗口，注摘要
    """

    def __init__(self, model: str = "gpt-4o", compact_threshold: float = 0.6, new_window_threshold: float = 0.8):
        self.model = model
        self.limit = MODEL_LIMITS.get(model, MODEL_LIMITS["unknown"])
        self.compact_threshold = compact_threshold
        self.new_window_threshold = new_window_threshold

    # ── Token 估算 ──

    def estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数（混合中英文）"""
        if not text:
            return 0
        # 中文字符 ≈ 1.5 token/char, 英文 ≈ 0.25 token/char
        chinese_chars = len(re.findall(r'[一-鿿㐀-䶿]', text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars * 0.25)

    def estimate_messages(self, messages: list[dict]) -> int:
        """估算消息列表的总 token 数"""
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += self.estimate_tokens(content)
            elif isinstance(content, dict):
                total += self.estimate_tokens(str(content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        total += self.estimate_tokens(part.get("text", "") or "")
                        # 图片 token（粗略估算：每张 ~1000 tokens）
                        if part.get("type") == "image_url":
                            total += 1000
        # 每条消息 ~4 tokens 开销（role + formatting）
        total += len(messages) * 4
        return total

    # ── 阈值检测 ──

    def check(self, messages: list[dict], system_prompt: str = "") -> ContextReport:
        """检查上下文是否需要压缩或新窗口"""
        total = self.estimate_messages(messages)
        if system_prompt:
            total += self.estimate_tokens(system_prompt)

        ratio = total / self.limit if self.limit > 0 else 0.0
        report = ContextReport(
            estimated_tokens=total,
            model_limit=self.limit,
            usage_ratio=ratio,
            messages_before=len(messages),
        )

        if ratio >= self.new_window_threshold:
            report.action = WindowAction.NEW_WINDOW
        elif ratio >= self.compact_threshold:
            report.action = WindowAction.COMPACT

        return report

    # ── 压缩 ──

    def compact(self, messages: list[dict], keep_first: int = 2, keep_last: int = 3) -> tuple[list[dict], str]:
        """压缩消息历史

        策略:
        - 保留前 keep_first 条（系统 prompt 相关）
        - 中间消息 → 生成摘要（去掉 tool_result/thinking/图片 base64）
        - 保留后 keep_last 条（最近上下文）
        - 裁剪所有非保留消息中的 base64 图片数据

        Returns:
            (compacted_messages, summary_string)
        """
        if len(messages) <= keep_first + keep_last:
            return messages, ""

        head = messages[:keep_first]
        tail = messages[-keep_last:] if keep_last > 0 else []
        middle_end = -keep_last if keep_last > 0 else len(messages)
        middle = messages[keep_first:middle_end]

        summary = self._summarize(middle)

        compacted = list(head)
        compacted.append({
            "role": "system",
            "content": f"[上下文压缩摘要 — {len(middle)} 条消息被压缩] {summary}",
        })
        # 裁剪 tail 中的 base64——只保留文本
        trimmed_tail = [self._strip_base64(m) for m in tail]
        compacted.extend(trimmed_tail)

        return compacted, summary

    def _strip_base64(self, message: dict) -> dict:
        """移除消息中的 base64 图片数据，只保留文本"""
        content = message.get("content", {})
        if isinstance(content, dict):
            # 如果是 dict 型 content（如 Agent 返回的结构化数据），保留结构但去掉图片字段
            cleaned = {}
            for k, v in content.items():
                if k in ("base64_data", "image_url"):
                    cleaned[k] = f"[已裁剪-{len(str(v))}字节]"
                elif isinstance(v, list):
                    cleaned[k] = [
                        self._strip_images_from_item(item) if isinstance(item, dict) else item
                        for item in v
                    ]
                else:
                    cleaned[k] = v
            return {**message, "content": cleaned}
        return message

    def _strip_images_from_item(self, item: dict) -> dict:
        """单条 item 中裁剪图片字段"""
        cleaned = {}
        for k, v in item.items():
            if k in ("base64_data", "image_url"):
                cleaned[k] = f"[已裁剪]"
            else:
                cleaned[k] = v
        return cleaned

    def _summarize(self, messages: list[dict]) -> str:
        """从消息列表中提取关键信息生成摘要"""
        points = []
        for m in messages:
            role = m.get("role", "")
            sender = m.get("sender", "")
            content = m.get("content", {})

            if isinstance(content, dict):
                # 提取关键字段
                if "category" in content:
                    points.append(f"分析品类={content['category']}")
                if "verdict" in content:
                    points.append(f"审查判定={content['verdict']}({content.get('overall_score', '?')}分)")
                if "passed" in content:
                    passed = "通过" if content["passed"] else "不通过"
                    points.append(f"合规={passed}")
                if "images" in content and isinstance(content["images"], list):
                    points.append(f"生图{len(content['images'])}张")
                if "main_image" in content:
                    points.append("生成提示词完成")
                if "action" in content and content["action"] == "done":
                    points.append("任务完成")

            # 提取错误
            if "error" in str(content):
                err = content.get("error", "") if isinstance(content, dict) else str(content)
                points.append(f"错误: {str(err)[:100]}")

        if not points:
            return f"共 {len(messages)} 条无关键数据消息"
        return " | ".join(points[:10])

    # ── 新窗口过渡 ──

    def new_window_summary(self, messages: list[dict]) -> str:
        """为旧窗口生成摘要，注入新窗口 system prompt"""
        _, summary = self.compact(messages, keep_first=0, keep_last=0)
        return summary or f"上一个窗口共 {len(messages)} 条消息"
