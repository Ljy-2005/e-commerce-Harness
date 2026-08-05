"""上下文管理器测试 — Token 估算 + 阈值检测 + 压缩"""

import pytest
from src.harness.context_manager import ContextManager, WindowAction, MODEL_LIMITS


class TestTokenEstimation:
    def test_chinese_text(self):
        cm = ContextManager()
        tokens = cm.estimate_tokens("这是一段中文测试文本用于估算token数量")
        assert tokens > 10

    def test_english_text(self):
        cm = ContextManager()
        tokens = cm.estimate_tokens("This is a test sentence for token estimation.")
        assert 5 < tokens < 30

    def test_empty_text(self):
        cm = ContextManager()
        assert cm.estimate_tokens("") == 0

    def test_messages_with_images(self):
        cm = ContextManager()
        messages = [
            {"role": "user", "content": "分析这张图片"},
            {"role": "user", "content": [
                {"type": "text", "text": "这是什么？"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc123"}},
            ]},
        ]
        tokens = cm.estimate_messages(messages)
        assert tokens > 1000  # at least image tokens counted


class TestThresholdDetection:
    def test_below_threshold_no_action(self):
        cm = ContextManager(model="gpt-4o")  # 128K limit
        messages = [{"role": "user", "content": "hello"}] * 5
        report = cm.check(messages)
        assert report.action == WindowAction.NONE
        assert report.usage_ratio < cm.compact_threshold

    def test_compact_threshold_triggers(self):
        cm = ContextManager(model="gpt-4o", compact_threshold=0.0)  # always trigger
        messages = [{"role": "user", "content": "hello"}] * 10
        report = cm.check(messages)
        assert report.action == WindowAction.COMPACT

    def test_new_window_threshold_triggers(self):
        cm = ContextManager(model="gpt-4o", new_window_threshold=0.0)  # always trigger
        messages = [{"role": "user", "content": "hello"}]
        report = cm.check(messages)
        assert report.action == WindowAction.NEW_WINDOW

    def test_with_system_prompt(self):
        cm = ContextManager(model="gpt-4o", compact_threshold=0.001)
        messages = [{"role": "user", "content": "hello"}]
        long_prompt = "x" * 10000
        report = cm.check(messages, system_prompt=long_prompt)
        assert report.estimated_tokens > cm.estimate_messages(messages)


class TestCompaction:
    def test_compact_reduces_messages(self):
        cm = ContextManager()
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "assistant", "content": "response 1"},
            {"role": "user", "content": "hello 1"},
            {"role": "assistant", "content": "response 2"},
            {"role": "user", "content": "hello 2"},
            {"role": "assistant", "content": "response 3"},
            {"role": "user", "content": "hello 3"},
            {"role": "assistant", "content": "final response"},
        ]
        compacted, summary = cm.compact(messages, keep_first=1, keep_last=2)
        assert len(compacted) < len(messages)
        assert len(summary) > 0

    def test_compact_no_need_when_few_messages(self):
        cm = ContextManager()
        messages = [{"role": "user", "content": "hello"}]
        compacted, summary = cm.compact(messages, keep_first=2, keep_last=3)
        assert len(compacted) == len(messages)  # unchanged
        assert summary == ""

    def test_summary_includes_key_fields(self):
        cm = ContextManager()
        messages = [
            {"role": "agent", "sender": "商品分析员", "content": {"category": "保健品", "confidence_score": 85}},
            {"role": "agent", "sender": "审查员", "content": {"verdict": "pass", "overall_score": 82}},
        ]
        _, summary = cm.compact(messages, keep_first=0, keep_last=0)
        assert "保健品" in summary

    def test_new_window_summary(self):
        cm = ContextManager()
        messages = [{"role": "agent", "content": {"category": "保健品"}}] * 5
        summary = cm.new_window_summary(messages)
        assert len(summary) > 0


class TestModelLimits:
    def test_known_models(self):
        assert MODEL_LIMITS["gpt-4o"] == 128_000
        assert MODEL_LIMITS["claude-sonnet-4-20250514"] == 200_000
        assert MODEL_LIMITS["qwen-max"] == 32_000

    def test_unknown_model_fallback(self):
        assert MODEL_LIMITS.get("some-new-model", MODEL_LIMITS["unknown"]) == 128_000

    def test_custom_model(self):
        cm = ContextManager(model="unknown-model")
        assert cm.limit == 128_000
