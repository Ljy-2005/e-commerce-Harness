"""价格表测试（`src/harness/pricing.py`）— 「事实优先 + 可标定」的核心钉子

用户的质疑（已核实成立）："不同的模型的花费是不同的，其次每个模型的花费又会随着时间
被各大模型商来回修改，这意味着除非每次模型商修改价格的时候都及时更新修改不然会出现
很大的误导。"

本文件钉住四件事：
1. **未标定的模型一律 `None`**（不许有兜底常量）——
   尤其 `ark` + `doubao-seedream-5-0-260128`（我们实际在用的方舟模型，
   出图 2048x2048）：旧代码会返回 `0.08`/`0.04`（DALL·E 3 的尺寸价表 + 兜底常量）；
2. 有公开参考价的模型能算出数字且 `basis` 可读；
3. 用户填写的价格覆盖内置参考价，改完**立刻生效**（mtime 缓存失效）；
4. `staleness()` 与 `observed_models()` 的边界（过期提示 / 没有审计文件不抛）。
"""

import json
from datetime import date, timedelta

import pytest

from src.harness import pricing
from src.harness.pricing import (
    DEVIATION_THRESHOLD,
    STALE_DAYS,
    calibrate,
    estimate,
    observed_models,
    price_table,
    resolve_price,
    save_prices,
    staleness,
)


@pytest.fixture(autouse=True)
def _restore_price_table():
    """每个用例前后清缓存 + **还原 `config/pricing.yaml`**（用例会写用户价格）

    写盘落在 conftest 的 `ECOMM_PROJECT_ROOT` 副本里（不动真实仓库），但同一批用例共用
    这份副本 —— 不还原的话，"用户填了方舟单价"的用例会让后面的"未标定"用例失败。
    """
    path = pricing.pricing_path()
    original = path.read_bytes() if path.exists() else None
    pricing._reset_cache()
    yield
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(original)
    pricing._reset_cache()


# ── 1. 未标定 → None（核心修复点） ──


class TestUncalibratedIsNone:
    def test_ark_seedream_has_no_price(self):
        """方舟 Seedream：没有可核对的公开价目表 → 查不到价格"""
        assert resolve_price("ark", "doubao-seedream-5-0-260128", "image") is None
        assert resolve_price("ark", "doubao-seedream-5-0-pro-260628", "image") is None
        assert resolve_price("ark", "doubao-seedream-4-5-251128", "image") is None
        assert resolve_price("ark", "doubao-seedream-4-0-250828", "image") is None

    def test_legacy_seedream_has_no_price(self):
        """旧火山视觉通道的 seedream-* 同样不给数字（此前是 `return 0.0`）"""
        assert resolve_price("seedream", "seedream-5.0", "image") is None
        assert resolve_price("seedream", "seedream-4.0", "image") is None

    def test_seedream_estimate_amount_is_none(self):
        result = estimate("ark", "doubao-seedream-5-0-260128", "image", {"images": 2})
        assert result["amount"] is None
        assert result["basis"] == "2 张图（价格未标定）"
        assert result["stale"] is False

    def test_regression_ark_seedream_2_images_is_none(self):
        """**回归钉子**：旧代码对 2 张方舟图返回 0.08（1024x1792 档）或 0.04（兜底）

        `OpenAIImageProvider._estimate_cost(size)` 只认 DALL·E 的三种尺寸，
        2048x2048 没命中任何键 → 落到兜底 0.04；`cost_tracker.record_image` 的
        未知模型兜底也是 0.04。两处都在"给没价的东西编价"。
        """
        assert estimate("ark", "doubao-seedream-5-0-260128", "image",
                        {"images": 2})["amount"] is None
        # 尺寸也不能成为"猜价"的入口
        assert estimate("ark", "doubao-seedream-5-0-260128", "image",
                        {"images": 1, "size": "2048x2048"})["amount"] is None

    def test_unknown_model_text_is_none(self):
        """不认识的文本模型不再按 (1.0, 3.0)/1M 凭空估价"""
        result = estimate("openrouter", "some-brand-new-model", "text",
                          {"tokens_in": 1_000_000, "tokens_out": 0})
        assert result["amount"] is None
        assert "价格未标定" in result["basis"]

    def test_builtin_table_has_no_seedream_numbers(self):
        """内置参考价里**根本不该出现** seedream 条目（防止有人手滑加回来）"""
        keys = " ".join(pricing.BUILTIN_REFERENCE_PRICES)
        assert "seedream" not in keys.lower()
        assert "doubao" not in keys.lower()
        assert "ark" not in keys.lower()


# ── 2. 有参考价 → 能算且可读 ──


class TestBuiltinReferencePrices:
    def test_dall_e_3_base_size(self):
        price = resolve_price("openai", "dall-e-3", "image")
        assert price is not None
        assert price["unit"] == "image"
        assert price["currency"] == "USD"
        assert price["source"] == pricing.SOURCE_BUILTIN
        assert price["updated_at"]  # 必须带更新日期

        result = estimate("openai", "dall-e-3", "image",
                          {"images": 2, "size": "1024x1024"})
        assert result["amount"] == pytest.approx(0.08)
        assert result["basis"] == "2 张图 × $0.04/张"

    def test_dall_e_3_large_size_is_priced_by_size(self):
        """按尺寸分档（这正是旧代码唯一做对的部分，保留）"""
        assert estimate("openai", "dall-e-3", "image",
                        {"images": 1, "size": "1024x1792"})["amount"] == pytest.approx(0.08)
        assert estimate("openai", "dall-e-3", "image",
                        {"images": 1, "size": "1792x1024"})["amount"] == pytest.approx(0.08)

    def test_unknown_size_does_not_fall_back_to_a_constant(self):
        """未标定的尺寸不再回落 0.04：本条目的基准价是 1024x1024 的 0.04，

        但**别的模型**的未知尺寸不能借它的价（下面 `ark` 的用例已覆盖）。
        这里钉住"尺寸缺失时用条目自己的基准价，而不是全局兜底常量"。
        """
        result = estimate("openai", "dall-e-3", "image", {"images": 1, "size": "512x512"})
        assert result["amount"] == pytest.approx(0.04)
        assert result["basis"] == "1 张图 × $0.04/张"

    def test_gpt_4o_text(self):
        price = resolve_price("openai", "gpt-4o", "text")
        assert price["unit"] == "1M_tokens"
        assert (price["in"], price["out"]) == (2.50, 10.00)

        result = estimate("openai", "gpt-4o", "text",
                          {"tokens_in": 1200, "tokens_out": 300})
        assert result["amount"] == pytest.approx((1200 / 1e6) * 2.50 + (300 / 1e6) * 10.00)
        assert result["basis"] == "1200 in + 300 out"
        assert result["updated_at"] == price["updated_at"]

    def test_capability_unit_mismatch_is_none(self):
        """给文本模型查 image 价 → 单位不自洽，按"查不到"处理（不拿错单位算钱）"""
        assert resolve_price("openai", "gpt-4o", "image") is None

    def test_flux_per_image(self):
        result = estimate("flux", "flux.1-dev", "image", {"images": 3})
        assert result["amount"] == pytest.approx(0.15)
        assert result["basis"] == "3 张图 × $0.05/张"

    def test_every_builtin_entry_has_date_and_note(self):
        """纪律：内置条目必须写清"什么时候核实的 + 依据"（用户要求带来源/更新日期）"""
        for key, entry in pricing.BUILTIN_REFERENCE_PRICES.items():
            assert entry.get("updated_at"), f"{key} 缺 updated_at"
            assert entry.get("note"), f"{key} 缺依据注释"
            assert entry.get("unit") in ("image", "1M_tokens"), key


# ── 3. 用户价格优先 + 改完立刻生效 ──


class TestUserOverrides:
    def test_user_entry_overrides_builtin(self):
        save_prices({"gpt-4o": {"unit": "1M_tokens", "in": 1.0, "out": 2.0,
                                "currency": "CNY", "updated_at": "2026-09-18"}})
        price = resolve_price("openai", "gpt-4o", "text")
        assert price["source"] == pricing.SOURCE_USER
        assert price["currency"] == "CNY"
        assert price["in"] == 1.0 and price["out"] == 2.0

    def test_user_entry_for_uncalibrated_model_makes_it_priced(self):
        """用户自己填了方舟单价 → 立刻有金额（这是设置页存在的意义）"""
        assert estimate("ark", "doubao-seedream-5-0-260128", "image",
                        {"images": 1})["amount"] is None

        save_prices({"ark/doubao-seedream-5-0-260128": {
            "unit": "image", "in": 0.2, "out": 0.2, "currency": "CNY",
            "updated_at": date.today().isoformat(), "note": "控制台账单核对"}})

        result = estimate("ark", "doubao-seedream-5-0-260128", "image", {"images": 2})
        assert result["amount"] == pytest.approx(0.4)
        assert result["currency"] == "CNY"
        assert result["basis"] == "2 张图 × ¥0.2/张"
        assert result["source"] == pricing.SOURCE_USER

    def test_route_scoped_entry_beats_wildcard(self):
        save_prices({
            "gpt-4o": {"unit": "1M_tokens", "in": 1.0, "out": 1.0},
            "openrouter/gpt-4o": {"unit": "1M_tokens", "in": 9.0, "out": 9.0},
        })
        assert resolve_price("openrouter", "gpt-4o", "text")["in"] == 9.0
        assert resolve_price("somewhere", "gpt-4o", "text")["in"] == 1.0

    def test_change_takes_effect_immediately(self):
        """配置改了立刻生效（mtime 缓存失效）—— 本仓库踩过"每次重新解析 YAML"的坑"""
        save_prices({"my-model": {"unit": "1M_tokens", "in": 1.0, "out": 2.0}})
        assert estimate("any-route", "my-model", "text",
                        {"tokens_in": 1_000_000})["amount"] == pytest.approx(1.0)

        save_prices({"my-model": {"unit": "1M_tokens", "in": 3.0, "out": 6.0}})
        assert estimate("any-route", "my-model", "text",
                        {"tokens_in": 1_000_000})["amount"] == pytest.approx(3.0)

    def test_price_table_reports_both_sources(self):
        table = price_table()
        assert table["path"] == pricing.PRICING_REL
        assert table["builtin_entry_count"] > 0
        assert table["user_entry_count"] >= 0
        assert "gpt-4o" in table["entries"]


class TestSavePricesValidation:
    @pytest.mark.parametrize("bad", [
        {"m": {"unit": "per_second", "in": 1}},            # unit 非法
        {"m": {"unit": "image", "in": -1}},                # 负价
        {"m": {"unit": "image", "in": "免费"}},             # 非数字
        {"m": {"unit": "image", "in": 1, "currency": "EUR"}},  # 币种不在白名单
        {"m": {"unit": "image", "in": 1, "updated_at": "昨天"}},  # 日期格式
        {"m": {"unit": "image"}},                          # 既没 in 也没 out
        {"m": {"unit": "image", "in": 1, "unknown_field": 1}},  # 白名单之外
        {"": {"unit": "image", "in": 1}},                  # 空键
        {"m": "0.04"},                                     # 条目不是对象
    ])
    def test_invalid_values_raise(self, bad):
        with pytest.raises(ValueError):
            save_prices(bad)

    def test_empty_payload_raises(self):
        with pytest.raises(ValueError):
            save_prices({})

    def test_invalid_entry_is_not_written(self):
        with pytest.raises(ValueError):
            save_prices({"bad-model": {"unit": "image", "in": -1}})
        assert resolve_price("", "bad-model", "image") is None


# ── 4. 标定与过期提示 ──


class TestCalibrate:
    def test_calibrate_image_from_actual_amount(self):
        price = calibrate("ark", "doubao-seedream-5-0-260128", "image",
                          actual_amount=0.6, usage={"images": 3, "size": "2048x2048"})
        assert price["source"] == pricing.SOURCE_CALIBRATED
        assert price["in"] == pytest.approx(0.2)

        result = estimate("ark", "doubao-seedream-5-0-260128", "image", {"images": 3})
        assert result["amount"] == pytest.approx(0.6)
        assert result["source"] == pricing.SOURCE_CALIBRATED

    def test_calibrate_requires_usage(self):
        with pytest.raises(ValueError):
            calibrate("deepseek", "deepseek-v4-flash", "text", 1.0, {})

    def test_calibrate_rejects_bad_capability(self):
        with pytest.raises(ValueError):
            calibrate("ark", "m", "audio", 1.0, {"images": 1})


class TestStaleness:
    def test_stale_after_90_days(self):
        old = (date.today() - timedelta(days=STALE_DAYS + 10)).isoformat()
        save_prices({"ancient-model": {"unit": "1M_tokens", "in": 1.0, "out": 1.0,
                                       "updated_at": old}})
        notices = staleness()
        hit = [n for n in notices if n["model"] == "ancient-model"]
        assert hit, "超过 90 天必须给过期提示"
        assert "未更新" in hit[0]["reason"]
        assert hit[0]["updated_at"] == old

        result = estimate("", "ancient-model", "text", {"tokens_in": 1000})
        assert result["stale"] is True

    def test_fresh_entry_is_not_stale(self):
        save_prices({"fresh-model": {"unit": "1M_tokens", "in": 1.0, "out": 1.0,
                                     "updated_at": date.today().isoformat()}})
        assert [n for n in staleness() if n["model"] == "fresh-model"] == []
        assert estimate("", "fresh-model", "text",
                        {"tokens_in": 10})["stale"] is False

    def test_deviation_from_measured_price_is_reported(self):
        """价格表被改成与实测标定偏差 >30% → 提示（规则 3）"""
        calibrate("ark", "doubao-seedream-5-0-260128", "image", 0.2, {"images": 1})
        # 标定后立刻改成一个偏离 100% 的值
        save_prices({"ark/doubao-seedream-5-0-260128": {
            "unit": "image", "in": 0.4, "out": 0.4, "updated_at": date.today().isoformat(),
            "measured_unit": 0.2, "measured_at": date.today().isoformat()}})
        notices = staleness()
        hit = [n for n in notices if n["model"] == "doubao-seedream-5-0-260128"]
        assert hit
        assert f">{int(DEVIATION_THRESHOLD * 100)}%" in hit[0]["reason"]

    def test_small_deviation_is_silent(self):
        save_prices({"close-model": {
            "unit": "image", "in": 0.21, "out": 0.21,
            "updated_at": date.today().isoformat(),
            "measured_unit": 0.2, "measured_at": date.today().isoformat()}})
        assert [n for n in staleness() if n["model"] == "close-model"] == []


# ── 5. 审计聚合（设置页的候选清单） ──


class TestObservedModels:
    def test_no_audit_dir_returns_empty(self, tmp_path, monkeypatch):
        """没有审计文件 → `[]`，不抛异常

        **确定性隔离**：同时改环境变量与模块内的 `data_root` 绑定 —— 只改环境变量时，
        全量跑（其它目录的用例会直接写 `os.environ`，见 conftest 的 `_restore_credential_env` 说明）
        出现过一次不确定失败；直接钉住 `data_root` 后不再依赖环境时序。
        """
        monkeypatch.setenv("ECOMM_DATA_DIR", str(tmp_path / "empty-data"))
        monkeypatch.setattr(pricing, "data_root", lambda: tmp_path / "empty-data")
        assert observed_models() == []

    def test_missing_data_dir_returns_empty(self, tmp_path, monkeypatch):
        """数据根目录本身不存在 → 同样是 `[]`（设置页不该因此崩）"""
        monkeypatch.setenv("ECOMM_DATA_DIR", str(tmp_path / "nope" / "deeper"))
        monkeypatch.setattr(pricing, "data_root", lambda: tmp_path / "nope" / "deeper")
        assert observed_models() == []

    def test_aggregates_route_model_capability(self, tmp_path, monkeypatch):
        audit_dir = tmp_path / "audit"
        audit_dir.mkdir()
        rows = [
            {"provider": "ark", "model": "doubao-seedream-5-0-260128", "tokens": 0,
             "timestamp": "2026-09-18T01:00:00+00:00"},
            {"provider": "ark", "model": "doubao-seedream-5-0-260128", "tokens": 0,
             "timestamp": "2026-09-18T02:00:00+00:00"},
            {"provider": "openai", "model": "gpt-4o", "tokens": 1200,
             "timestamp": "2026-09-18T03:00:00+00:00"},
            {"provider": "deepseek", "model": "deepseek-v4-flash-vision-exp", "tokens": 900,
             "timestamp": "2026-09-18T04:00:00+00:00"},
            {"provider": "openai", "model": "", "tokens": 5},          # 无模型 → 跳过
            "not-a-dict",
        ]
        (audit_dir / "audit-2026-09-18.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n{bad json\n",
            encoding="utf-8")
        monkeypatch.setenv("ECOMM_DATA_DIR", str(tmp_path))

        found = {(m["route"], m["model"]): m for m in observed_models()}
        assert found[("ark", "doubao-seedream-5-0-260128")]["calls"] == 2
        assert found[("ark", "doubao-seedream-5-0-260128")]["capability"] == "image"
        # 未标定 → priced=False，设置页据此提示"这个模型没有价格"
        assert found[("ark", "doubao-seedream-5-0-260128")]["priced"] is False
        assert found[("openai", "gpt-4o")]["capability"] == "text"
        assert found[("openai", "gpt-4o")]["priced"] is True
        assert found[("deepseek", "deepseek-v4-flash-vision-exp")]["capability"] == "vision"
        assert found[("openai", "gpt-4o")]["last_seen"] == "2026-09-18T03:00:00+00:00"
        assert len(found) == 3, "空模型/坏行都要跳过"
