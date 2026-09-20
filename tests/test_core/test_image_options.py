"""生图质量策略（config/image.yaml）—— 文本策略 / 参考图模式 / 水印 / 尺寸档位

背景（用户三点反馈）：① 生图必须"文+图"而不是单纯文生图/图生图；② 包装文字要么逐字还原
要么干净虚化，不能让模型自由编造；③ 交付物要成套图。这些都需要**可配置**，而且配置要能
在设置页改（用户此前反复反馈"没法自己设置"）。

落盘策略沿用已确认的 `config/chat.yaml` 模式：UI 写入独立文件，**绝不改写
`config/models.yaml`** —— `yaml.safe_dump` 会把文件里的注释全部丢掉，而那份注释本身就是文档。
"""

import pytest

from src.core import config as cfg_mod


@pytest.fixture
def isolated_image_config(tmp_path, monkeypatch):
    """把生图策略覆盖文件指到每测试独立的 tmp 文件（避免测试间互相串）"""
    path = tmp_path / "image.yaml"
    monkeypatch.setattr(cfg_mod, "IMAGE_CONFIG_REL", str(path))
    return path


class TestImageOptionDefaults:
    def test_defaults_are_conservative(self, isolated_image_config):
        """没有覆盖文件时的默认值：不加水印、有原图就用参考图、文字默认逐字还原"""
        options = cfg_mod.image_options()
        assert options["text_strategy"] == "preserve"
        assert options["reference_mode"] == "auto"
        assert options["watermark"] is False
        assert options["max_references"] == 4
        # 新路径（套图）每槽 1 张；旧路径（无 set_plan）保持 variants=3 的历史行为
        assert options["slot_candidates"] == 1
        assert options["variants"] == 3

    def test_quality_thresholds_present(self, isolated_image_config):
        quality = cfg_mod.image_options()["quality"]
        assert quality["edge_whiteness"] > 0
        assert quality["watermark_zone_delta"] > 0
        assert 0 < quality["identity_similarity_min"] < 1
        assert 0 < quality["near_copy_max"] <= 1


class TestSizePresets:
    """方舟 Seedream 支持 2K/3K/4K 档位；此前只认 `WxH`，用户只能写死像素"""

    @pytest.mark.parametrize("raw,expected", [
        ("2k", "2K"), ("2K", "2K"), (" 4k ", "4K"), ("1K", "1K"), ("3K", "3K"),
        ("2048x2048", "2048x2048"), ("1024X1792", "1024x1792"),
    ])
    def test_valid(self, raw, expected):
        assert cfg_mod.clean_image_size(raw) == expected

    @pytest.mark.parametrize("bad", ["", None, "5K", "2K; rm -rf /", "K", "xx", "10x10",
                                     "99999x99999", "abc"])
    def test_invalid(self, bad):
        assert cfg_mod.clean_image_size(bad) == ""

    def test_preset_resolves_from_config(self, isolated_image_config, monkeypatch):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {"size": "2K"}}})
        assert cfg_mod.resolve_image_size("2048x2048") == "2K"


class TestStrategyValidation:
    @pytest.mark.parametrize("raw,expected", [
        ("preserve", "preserve"), ("BLUR", "blur"), (" none ", "none"),
        ("", "preserve"), (None, "preserve"), ("whatever", "preserve"),
    ])
    def test_text_strategy(self, raw, expected):
        assert cfg_mod.clean_text_strategy(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("auto", "auto"), ("OFF", "off"), ("", "auto"), (None, "auto"), ("x", "auto"),
    ])
    def test_reference_mode(self, raw, expected):
        assert cfg_mod.clean_reference_mode(raw) == expected

    def test_models_yaml_can_set_strategy(self, isolated_image_config, monkeypatch):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {"text_strategy": "blur",
                                                                "reference_mode": "off"}}})
        options = cfg_mod.image_options()
        assert options["text_strategy"] == "blur"
        assert options["reference_mode"] == "off"

    def test_invalid_models_yaml_values_fall_back(self, isolated_image_config, monkeypatch):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {"text_strategy": "编造",
                                                                "max_references": "很多"}}})
        options = cfg_mod.image_options()
        assert options["text_strategy"] == "preserve"
        assert options["max_references"] == 4


class TestSaveImageSettings:
    def test_save_and_read_back(self, isolated_image_config):
        cfg_mod.save_image_settings({"text_strategy": "blur", "watermark": True})
        assert isolated_image_config.exists()
        options = cfg_mod.image_options()
        assert options["text_strategy"] == "blur"
        assert options["watermark"] is True

    def test_unknown_field_rejected(self, isolated_image_config):
        with pytest.raises(ValueError):
            cfg_mod.save_image_settings({"nope": 1})

    @pytest.mark.parametrize("payload", [
        {"text_strategy": "乱写"},
        {"reference_mode": "乱写"},
        {"max_references": 999},
        {"max_references": 0},
        {"slot_candidates": 99},
        {"size": "2048x2048; rm -rf /"},
        {"watermark": "也许"},
    ])
    def test_invalid_value_rejected(self, isolated_image_config, payload):
        with pytest.raises(ValueError):
            cfg_mod.save_image_settings(payload)

    def test_partial_save_keeps_previous(self, isolated_image_config):
        cfg_mod.save_image_settings({"text_strategy": "none", "max_references": 2})
        cfg_mod.save_image_settings({"watermark": True})
        options = cfg_mod.image_options()
        assert options["text_strategy"] == "none"
        assert options["max_references"] == 2
        assert options["watermark"] is True

    def test_save_does_not_touch_models_yaml(self, isolated_image_config):
        """UI 写入绝不能落到 models.yaml —— 那会丢掉文件里全部注释（自身就是文档）"""
        from src.core.config import _project_root
        models = _project_root() / "config" / "models.yaml"
        before = models.read_bytes()
        cfg_mod.save_image_settings({"text_strategy": "blur"})
        assert models.read_bytes() == before

    def test_platform_slot_override(self, isolated_image_config):
        cfg_mod.save_image_settings({"platforms": {"taobao": {"slots": ["main_white"]}}})
        options = cfg_mod.image_options()
        assert options["platforms"]["taobao"]["slots"] == ["main_white"]

    def test_platform_slot_override_rejects_bad_shape(self, isolated_image_config):
        with pytest.raises(ValueError):
            cfg_mod.save_image_settings({"platforms": {"taobao": {"slots": "main_white"}}})
        with pytest.raises(ValueError):
            cfg_mod.save_image_settings({"platforms": "taobao"})


class TestTypography:
    """信息图排版样式（字号/条目数/配色/页脚）—— 用户看完成图后要能自己调"""

    def test_defaults(self, isolated_image_config):
        typo = cfg_mod.image_options()["typography"]
        assert typo["font_scale"] == 1.0
        assert typo["max_items"] == 6
        assert typo["brand_color"] == "#1860AC"
        assert typo["accent_color"] == "#24945F"
        assert typo["show_footer"] is True

    def test_save_and_read_back(self, isolated_image_config):
        cfg_mod.save_image_settings({"typography": {"font_scale": 1.3, "max_items": 4,
                                                    "brand_color": "#b02020",
                                                    "show_footer": False}})
        typo = cfg_mod.image_options()["typography"]
        assert typo["font_scale"] == 1.3
        assert typo["max_items"] == 4
        assert typo["brand_color"] == "#B02020"     # 统一大写
        assert typo["show_footer"] is False

    def test_partial_save_keeps_previous(self, isolated_image_config):
        cfg_mod.save_image_settings({"typography": {"font_scale": 1.5}})
        cfg_mod.save_image_settings({"typography": {"max_items": 3}})
        typo = cfg_mod.image_options()["typography"]
        assert typo["font_scale"] == 1.5 and typo["max_items"] == 3

    @pytest.mark.parametrize("payload", [
        {"font_scale": 5}, {"font_scale": 0.1}, {"font_scale": "大"},
        {"max_items": 0}, {"max_items": 99}, {"max_items": "多"},
        {"brand_color": "红色"}, {"brand_color": "#12345"}, {"accent_color": "1860AC"},
        {"show_footer": "也许"}, {"unknown": 1},
    ])
    def test_invalid_rejected(self, isolated_image_config, payload):
        with pytest.raises(ValueError):
            cfg_mod.save_image_settings({"typography": payload})

    def test_non_object_rejected(self, isolated_image_config):
        with pytest.raises(ValueError):
            cfg_mod.save_image_settings({"typography": "大一点"})

    def test_models_yaml_can_set_typography(self, isolated_image_config, monkeypatch):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {
                                "typography": {"font_scale": 0.8, "max_items": 2}}}})
        typo = cfg_mod.image_options()["typography"]
        assert typo["font_scale"] == 0.8 and typo["max_items"] == 2

    def test_override_file_wins(self, isolated_image_config, monkeypatch):
        monkeypatch.setattr(cfg_mod, "load_models_config",
                            lambda: {"capabilities": {"image": {
                                "typography": {"font_scale": 0.8}}}})
        cfg_mod.save_image_settings({"typography": {"font_scale": 1.6}})
        assert cfg_mod.image_options()["typography"]["font_scale"] == 1.6
