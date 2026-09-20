"""身份词治理与品牌色板（A70-A71）

用户实测取证：提示词把包装版式与品牌文字逐字写进画面描述 → 模型"按文字重画小字"
（`Sickle Ligament→Sadle Ligement`、手写体 `Schneiski` 乱码，身份相似度掉到 0.22–0.33）。
同时用户要求"要有高级感、让人一看就觉得牌子正规"——**品牌色值要写进提示词**
（色值是包装上的事实），文字不写。
"""

from src.harness.image_prompt import identity_terms_in_prompt, strip_identity_terms
from src.harness.product_identity import identity_terms, normalize_identity, palette_summary

ANALYSIS = {
    "product_identity": {
        "brand": "德國 樂美寶® / DEFOEBUENA",
        "product_name": "金裝 強力 肝迅康（升級版）",
        "spec": "60's",
        "certifications": ["德國GMP優質產品", "MADE IN GERMANY"],
        "package_form": "紙盒直盒",
        "confidence": 0.86,
        "evidence": "正面照",
        "brand_palette": {"primary": "#1B3F94", "secondary": "7CBF4A",
                          "background": "#EEF2F7",
                          "evidence": "正面左上藍色品牌區與綠色品名"},
    }
}


class TestIdentityTerms:
    def test_terms_include_brand_variants_but_drop_generic_words(self):
        identity = normalize_identity(ANALYSIS)
        terms = identity_terms(identity)
        assert "DEFOEBUENA" in terms and "樂美寶" in terms and "肝迅康" in terms
        assert "德國" not in terms          # 通用词，不算"复述包装"
        assert "IN" not in terms            # <3 字符 ASCII 会误伤英文提示词

    def test_strip_removes_terms_and_cleans_residue(self):
        identity = normalize_identity(ANALYSIS)
        text = ("純白背景，德國 樂美寶® / DEFOEBUENA 紙盒置中，"
                "MADE IN GERMANY、60's 標示清晰")
        cleaned, removed = strip_identity_terms(text, identity_terms(identity))
        assert "DEFOEBUENA" not in cleaned and "樂美寶" not in cleaned
        assert "GERMANY" not in cleaned and " IN " not in cleaned
        assert "®" not in cleaned
        assert removed, "移除项必须回传（记账，不静默）"

    def test_colors_are_not_treated_as_identity_terms(self):
        identity = normalize_identity(ANALYSIS)
        text = "品牌深蓝 #1B3F94 背景无缝，烫金细窄高光"
        assert identity_terms_in_prompt(text, identity_terms(identity)) == []
        assert strip_identity_terms(text, identity_terms(identity)) == (text, [])

    def test_missing_identity_yields_no_terms(self):
        assert identity_terms(normalize_identity({}, source="none")) == []


class TestBrandPalette:
    def test_palette_is_normalized_and_hex_validated(self):
        identity = normalize_identity(ANALYSIS)
        palette = identity["brand_palette"]
        assert palette["primary"] == "#1B3F94"
        assert palette["secondary"] == "#7CBF4A"     # 自动补 #
        assert palette["background"] == "#EEF2F7"
        assert "藍色品牌區" in palette["evidence"]

    def test_dirty_colors_are_dropped(self):
        identity = normalize_identity({"product_identity": {
            "brand": "X", "product_name": "Y", "confidence": 0.9,
            "brand_palette": {"primary": "red", "secondary": "#12", "background": None}}})
        assert identity["brand_palette"]["primary"] == ""
        assert palette_summary(identity["brand_palette"]) == ""

    def test_summary_lists_roles_and_source(self):
        identity = normalize_identity(ANALYSIS)
        text = palette_summary(identity["brand_palette"])
        assert "主色 #1B3F94" in text and "辅色 #7CBF4A" in text and "取自：" in text
