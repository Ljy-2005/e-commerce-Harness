"""商品身份卡 —— 从分析结果里提取并判定"是否已确认"

背景（用户反馈）："产品分析员根本没有识别到我喂的图是什么品牌，商品名是什么都没强调或者
提醒，这是一个很大的缺失"。实测 `config/prompts/analyst.yaml` 的字段里**没有品牌、没有
商品名、没有任何包装文字转录**，于是下游（提示词→生图→审查）全程没有事实基准。

另外用户担心"会不会把我这款商品套成别的牌子"——所以身份卡必须带**溯源**：
来源只能是本次会话的视觉识别或用户确认；mock / 记忆库 / 历史会话都不得冒充事实。
"""

import pytest

from src.harness.product_identity import (
    identity_card_block,
    identity_summary,
    is_confirmed,
    normalize_identity,
)


VISION_PAYLOAD = {
    "product_identity": {
        "brand": "DEFOEBUENA®",
        "product_name": "金裝強力肝迅康",
        "spec": "60's",
        "certifications": ["德國 GMP 優質產品"],
        "package_form": "紙盒+瓶",
        "confidence": 0.92,
        "evidence": "正面金色燙印條英文 + 藍色繁體品名",
    },
    "visible_text": {
        "lines": [{"text": "金裝強力肝迅康", "location": "正面中央", "legible": True},
                  {"text": "60's", "location": "正面右下", "legible": True}],
        "language": "zh-Hant",
        "has_illegible": False,
    },
}


class TestNormalize:
    def test_confirmed_when_brand_and_name_present(self):
        identity = normalize_identity(VISION_PAYLOAD, source="vision")
        assert identity["status"] == "confirmed"
        assert is_confirmed(identity)
        assert identity["brand"] == "DEFOEBUENA®"      # 原文保留，含 ®
        assert identity["product_name"] == "金裝強力肝迅康"  # 繁体原样
        assert identity["source"] == "vision"
        assert identity["visible_text"]["lines"][0]["text"] == "金裝強力肝迅康"

    def test_uncertain_when_brand_missing(self):
        payload = {"product_identity": {"brand": "", "product_name": "肝迅康",
                                        "confidence": 0.9}}
        identity = normalize_identity(payload, source="vision")
        assert identity["status"] == "uncertain"
        assert not is_confirmed(identity)
        assert "品牌" in identity["missing"]

    def test_uncertain_when_identity_block_absent(self):
        identity = normalize_identity({"category": "保健品"}, source="vision")
        assert identity["status"] == "uncertain"
        assert set(identity["missing"]) >= {"品牌", "商品名"}

    def test_low_confidence_is_not_confirmed(self):
        payload = {"product_identity": {"brand": "X", "product_name": "Y",
                                        "confidence": 0.2}}
        identity = normalize_identity(payload, source="vision")
        assert identity["status"] == "uncertain"

    @pytest.mark.parametrize("source", ["mock", "none"])
    def test_mock_or_missing_source_never_confirmed(self, source):
        """mock/无来源的数据**永远**不能算已确认 —— 否则会把演示数据当成本次商品事实"""
        identity = normalize_identity(VISION_PAYLOAD, source=source)
        assert identity["status"] == "uncertain"
        assert not is_confirmed(identity)

    def test_user_confirmed_beats_low_model_confidence(self):
        """人工确认过的（用户在暂停里填了品牌）即使模型置信度低也算已确认"""
        payload = {"product_identity": {"brand": "X", "product_name": "Y",
                                        "confidence": 0.1}}
        identity = normalize_identity(payload, source="user_confirmed")
        assert identity["status"] == "confirmed"

    def test_garbage_input_does_not_crash(self):
        for payload in (None, {}, {"product_identity": "文字"}, {"product_identity": {"brand": 5}},
                        {"visible_text": []}):
            identity = normalize_identity(payload, source="vision")
            assert identity["status"] in ("confirmed", "uncertain")
            assert isinstance(identity["brand"], str)

    def test_certifications_normalized_to_list(self):
        payload = {"product_identity": {"brand": "A", "product_name": "B",
                                        "certifications": "GMP", "confidence": 0.9}}
        assert normalize_identity(payload, source="vision")["certifications"] == ["GMP"]


class TestSummary:
    def test_confirmed_summary_is_prominent_and_verbatim(self):
        text = identity_summary(normalize_identity(VISION_PAYLOAD, source="vision"))
        assert "DEFOEBUENA®" in text and "金裝強力肝迅康" in text
        assert "60's" in text
        assert text.startswith("✅")

    def test_uncertain_summary_says_what_is_missing(self):
        text = identity_summary(normalize_identity({"category": "保健品"}, source="vision"))
        assert text.startswith("⚠️")
        assert "品牌" in text and "商品名" in text

    def test_mock_summary_is_labeled_as_demo_data(self):
        text = identity_summary(normalize_identity(VISION_PAYLOAD, source="mock"))
        assert "演示数据" in text


class TestCardBlock:
    def test_confirmed_block_is_a_fact_baseline(self):
        block = identity_card_block(normalize_identity(VISION_PAYLOAD, source="vision"))
        assert "DEFOEBUENA®" in block
        assert "逐字" in block
        # 必须明确禁止编造：表里没有的信息一律不得出现
        assert "不得" in block

    def test_uncertain_block_forbids_text_generation(self):
        block = identity_card_block(normalize_identity({"category": "保健品"}, source="vision"))
        assert "未确认" in block
        assert "虚化" in block          # 无事实基准时只能虚化，不能编
        assert "禁止" in block

    def test_mock_block_flags_demo_data(self):
        block = identity_card_block(normalize_identity(VISION_PAYLOAD, source="mock"))
        assert "演示数据" in block
