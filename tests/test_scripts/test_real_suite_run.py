"""真实套图验收脚本的纯逻辑（成本闸门 + 张数闸门 + 报告汇总）

真跑（会花钱）由用户决定；这里钉住四件容易出错的事：
1. **张数硬闸门**（`--max-images`，事实量）：金额依赖价格表，未标定的模型
   （方舟 Seedream 没有可核对的公开价目表）**不计入** `cost_so_far`，
   只靠 `--max-cost` 会在"未标定 + 循环出图"时放任烧钱；
2. **成本闸门**（`--max-cost`，按价格表估算、未标定不计）；
3. **报告汇总**：把会话产物整理成"每个槽位出没出、缺素材的为什么缺、体检/审查结论"；
4. **未标定金额**：报告里"已标定部分"与"另有 N 次未标定"分开，不写假金额。
"""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "scripts" / "real_suite_run.py"
    spec = importlib.util.spec_from_file_location("real_suite_run", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["real_suite_run"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return _load()


class TestCostGate:
    def test_exceeded(self, runner):
        assert runner.cost_exceeded(3.0, 3.0) is True
        assert runner.cost_exceeded(3.01, 3.0) is True
        assert runner.cost_exceeded(2.99, 3.0) is False

    def test_zero_means_unlimited(self, runner):
        assert runner.cost_exceeded(99.0, 0) is False
        assert runner.cost_exceeded(99.0, -1) is False


class TestImagesGate:
    """张数闸门：**不依赖价格表**的事实量上限"""

    def test_exceeded(self, runner):
        assert runner.images_exceeded(6, 6) is True
        assert runner.images_exceeded(7, 6) is True
        assert runner.images_exceeded(5, 6) is False

    def test_zero_means_unlimited(self, runner):
        assert runner.images_exceeded(999, 0) is False
        assert runner.images_exceeded(999, -1) is False

    def test_default_is_unlimited(self, runner):
        assert runner.DEFAULT_MAX_IMAGES == 0
        assert runner.images_exceeded(10_000, runner.DEFAULT_MAX_IMAGES) is False


class TestSummarize:
    SESSION = {
        "session_id": "abc", "status": "waiting_human", "turn_count": 8, "cost_so_far": 0.4078,
        "artifacts": {
            "product_identity": {"status": "confirmed", "brand": "DEFOEBUENA®",
                                 "product_name": "金裝強力肝迅康", "spec": "60's",
                                 "source": "vision", "confidence": 0.86},
            "set_plan_coverage": {"expected": 5, "produced": 4, "done_slots": ["main_white"],
                                  "missing_slots": ["main_ingredients"],
                                  "blocked_slots": ["main_ingredients"], "complete": False},
            "images": [
                {"slot_id": "main_white", "saved_path": "d/s/main_white_1.jpg",
                 "text_status": "", "generation_params": {"reference_count": 1},
                 "quality": {"width": 2048, "height": 2048, "is_white_bg": True,
                             "watermark_suspected": False,
                             "reference_compare": {"identity_similarity": 0.56}}},
                {"slot_id": "main_ingredients", "text_status": "blocked",
                 "text_reason": "包装正面看不到成分表", "quality": {}},
            ],
            "quality_report": {"count": 4, "white_bg_ok": True, "watermark_free": True,
                               "identity_lost": ["main_spec"], "near_copy": []},
            "review": {"verdict": "retry", "overall_score": 75.0, "reference_compared": True,
                       "needs_human_review": True,
                       "fidelity_findings": [{"element": "品牌文字", "expected": "DEFOEBUENA®",
                                              "observed": "DEFOEBUENA®", "status": "same"}],
                       "top_issues": ["白底图丢失肝臟解剖圖"]},
            "compliance": {"passed": None, "risk_level": None},
            "error_history": [],
        },
    }

    def test_summary_fields(self, runner):
        report = runner.summarize_session(self.SESSION)
        assert report["session_id"] == "abc" and report["status"] == "waiting_human"
        assert report["cost_usd"] == 0.4078
        assert report["identity"]["brand"] == "DEFOEBUENA®"
        assert report["coverage"]["blocked_slots"] == ["main_ingredients"]
        assert len(report["images"]) == 2
        assert report["images"][0]["size"] == "2048x2048"
        assert report["images"][0]["identity_similarity"] == 0.56
        assert report["images"][1]["text_status"] == "blocked"
        assert report["review"]["verdict"] == "retry"
        assert report["fidelity_findings"][0]["status"] == "same"
        assert report["review_issues"] == ["白底图丢失肝臟解剖圖"]

    def test_empty_session_does_not_crash(self, runner):
        report = runner.summarize_session({})
        assert report["images"] == [] and report["status"] == ""
        assert report["identity"]["brand"] is None

    def test_errors_are_truncated(self, runner):
        session = {**self.SESSION,
                   "error_history": [{"error": "x" * 500}, {"error": "短"}]}
        report = runner.summarize_session(session)
        assert all(len(item) <= 160 for item in report["errors"])

    def test_report_splits_calibrated_and_unknown_cost(self, runner):
        """金额口径：`cost_usd` 只是已标定部分，未标定次数单独给（不并进金额）"""
        session = {**self.SESSION, "cost_unknown_calls": 3}
        report = runner.summarize_session(session)
        assert report["cost_usd"] == 0.4078
        assert report["cost_unknown_calls"] == 3
        assert report["images_count"] == 2, "张数是事实，必须单独给"

    def test_unknown_calls_defaults_to_zero(self, runner):
        report = runner.summarize_session(self.SESSION)
        assert report["cost_unknown_calls"] == 0


class TestResolveImageRoute:
    def test_reads_agent_override(self, runner, monkeypatch):
        monkeypatch.setattr(runner, "load_models_config", lambda: {
            "agent_overrides": {"生图员": {"image": "ark/doubao-seedream-5-0-260128"}},
            "capabilities": {"image": {"default": "openai/dall-e-3"}},
        })
        assert runner.resolve_image_route() == ("ark", "doubao-seedream-5-0-260128")

    def test_falls_back_to_capability_default(self, runner, monkeypatch):
        monkeypatch.setattr(runner, "load_models_config", lambda: {
            "capabilities": {"image": {"default": "openai/dall-e-3"}}})
        assert runner.resolve_image_route() == ("openai", "dall-e-3")

    def test_config_failure_is_not_fatal(self, runner, monkeypatch):
        def _boom():
            raise RuntimeError("配置读不到")

        monkeypatch.setattr(runner, "load_models_config", _boom)
        assert runner.resolve_image_route() == ("", "")


class TestDryRunOutput:
    """dry-run 的花费提示：未标定 → **打印张数而不是金额**（绝不拿常量冒充）"""

    def _image(self, tmp_path):
        from PIL import Image
        path = tmp_path / "正面.jpg"
        Image.new("RGB", (64, 64), (255, 0, 0)).save(path, format="JPEG")
        return path

    def _invoke(self, runner, tmp_path, monkeypatch, capsys, **extra):
        img = self._image(tmp_path)
        argv = ["real_suite_run.py", "--images", str(img), "--dry-run"]
        for key, value in extra.items():
            argv += [f"--{key.replace('_', '-')}", str(value)]
        monkeypatch.setattr(runner.sys, "argv", argv)
        code = runner.main()
        return code, capsys.readouterr().out

    def test_unpriced_prints_images_not_money(self, runner, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(runner, "resolve_image_route", lambda: ("ark", "doubao-seedream-5-0-260128"))
        code, out = self._invoke(runner, tmp_path, monkeypatch, capsys)
        assert code == 0
        assert "未标定" in out
        assert "张" in out and "张数闸门" in out
        assert "0.04" not in out, "不得再按 DALL·E 时代的 $0.04/张 预估"
        # 未标定时必须点明 --max-cost 形同虚设，建议用张数闸门兜底
        assert "--max-images" in out

    def test_priced_prints_amount_with_source(self, runner, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(runner, "resolve_image_route", lambda: ("openai", "dall-e-3"))
        code, out = self._invoke(runner, tmp_path, monkeypatch, capsys)
        assert code == 0
        assert "预计花费" in out and "USD" in out
        assert "内置参考价" in out, "有价时必须给来源"
        assert "按价格表估算、未标定模型不计" in out, "--max-cost 的打印要说清口径"

    def test_max_images_flag_is_reported(self, runner, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(runner, "resolve_image_route", lambda: ("ark", "doubao-seedream-5-0-260128"))
        code, out = self._invoke(runner, tmp_path, monkeypatch, capsys, max_images=4)
        assert code == 0
        assert "4 张即中止" in out
        assert "事实量" in out

