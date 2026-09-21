"""CLI 测试（test-plan P3 L1）— 参数解析 / Mock 跑通 / 错误路径 / 退出码

覆盖 `python -m src.cli run|batch|agents-list|config-validate`：
- 参数缺失（typer 用法错误 → exit 2）
- 文件不存在 / 非文件 / 坏图（P3 新增入口校验 → exit 1）
- Mock 模式全流程跑通（exit 0 + 产出物摘要）
- 平台透传（无枚举校验，Mock 下照常完成，属既有行为）
"""

from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

import src.cli as cli_mod

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_checkpoint_writes(monkeypatch):
    """CLI 会话运行会写 data/checkpoints —— 测试期间置为 no-op，防污染"""
    import src.storage.checkpoint as cp_mod

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(cp_mod, "save_checkpoint", _noop)
    monkeypatch.setattr(cp_mod, "delete_checkpoint", _noop)


@pytest.fixture(autouse=True)
def _fast_rate_limiter(monkeypatch):
    """注入高额限流器：全局 60rpm 会把每次完整群聊跑拖慢到 ~30s
    （限流器本身在 tests/test_harness/test_rate_limiter.py 有专门测试，
    沿用 tests/test_workflow/conftest.py 的既有隔离模式）"""
    import src.agents.base as agents_base
    from src.harness.rate_limiter import RateLimiter
    monkeypatch.setattr(agents_base, "_rate_limiter", RateLimiter(default_rpm=100_000))


def _make_jpeg(path: Path, size=(64, 64), color=(255, 0, 0)) -> Path:
    Image.new("RGB", size, color).save(path, format="JPEG")
    return path


class TestRunArgs:
    def test_missing_image_argument_exit_2(self):
        """必填参数缺失 → typer 用法错误（exit 2）"""
        result = runner.invoke(cli_mod.app, ["run"])
        assert result.exit_code == 2
        assert "Missing argument" in result.output

    def test_run_help_exit_0(self):
        result = runner.invoke(cli_mod.app, ["run", "--help"])
        assert result.exit_code == 0
        assert "商品图片路径" in result.output
        assert "--platform" in result.output
        assert "--mode" in result.output
        assert "--max-turns" in result.output

    def test_nonexistent_image_exit_1(self):
        result = runner.invoke(cli_mod.app, ["run", "no_such_file.jpg"])
        assert result.exit_code == 1
        assert "[ERROR] 图片不存在" in result.output

    def test_directory_as_image_exit_1(self, tmp_path):
        """目录不是文件 → 明确报错而非读目录崩溃"""
        result = runner.invoke(cli_mod.app, ["run", str(tmp_path)])
        assert result.exit_code == 1
        assert "[ERROR] 不是文件" in result.output

    def test_bad_image_rejected_exit_1(self, tmp_path):
        """P3 新增：坏图（不可解码）在 CLI 入口被拦截"""
        bad = tmp_path / "broken.jpg"
        bad.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)  # 截断 JPEG
        result = runner.invoke(cli_mod.app, ["run", str(bad)])
        assert result.exit_code == 1
        assert "[ERROR] 图片校验失败" in result.output


class TestRunMockFlow:
    def test_valid_image_mock_completes(self, tmp_path):
        """Mock 模式全流程：有效图片 → 群聊跑通 → 产出物摘要"""
        img = _make_jpeg(tmp_path / "product.jpg")
        result = runner.invoke(cli_mod.app, ["run", str(img)])
        assert result.exit_code == 0, result.output
        assert "[Done] session_id=" in result.output
        assert "status:" in result.output
        # Mock 模板产出摘要
        assert "[Analysis]" in result.output
        assert "[Review] score:" in result.output
        assert "[Compliance]" in result.output
        assert "--- Messages" in result.output

    def test_options_passed_to_session(self, tmp_path, monkeypatch):
        """--platform/--product-info/--category/--mode/--max-turns 透传到 SessionManager"""
        from src.chat import session as session_mod

        calls: list[dict] = []

        # 用包装类记录 create 参数并委托真实实现
        class RecordingSessionManager(session_mod.SessionManager):
            def create(self, *args, **kwargs):
                calls.append(kwargs)
                return super().create(*args, **kwargs)

        monkeypatch.setattr(session_mod, "SessionManager", RecordingSessionManager)
        img = _make_jpeg(tmp_path / "p.jpg")
        result = runner.invoke(cli_mod.app, [
            "run", str(img),
            "--platform", "amazon",
            "--product-info", "德国护肝胶囊",
            "--category", "保健品",
            "--mode", "serial",
            "--max-turns", "6",
        ])
        assert result.exit_code == 0, result.output
        assert calls, "SessionManager.create 未被调用"
        kw = calls[0]
        assert kw["platform"] == "amazon"
        assert kw["product_info"] == "德国护肝胶囊"
        assert kw["category_hint"] == "保健品"
        assert kw["collaboration_mode"] == "serial"
        assert kw["max_turns"] == 6

    def test_unknown_platform_passthrough(self, tmp_path):
        """平台参数无枚举校验（既有行为）：Mock 下照常完成，不报错"""
        img = _make_jpeg(tmp_path / "p.jpg")
        result = runner.invoke(cli_mod.app, ["run", str(img), "--platform", "nonexistent_platform"])
        assert result.exit_code == 0, result.output
        assert "[Done] session_id=" in result.output


class TestBatchCommand:
    def test_nonexistent_dir_exit_1(self):
        result = runner.invoke(cli_mod.app, ["batch", "no_such_dir"])
        assert result.exit_code == 1
        assert "[ERROR] 目录不存在" in result.output

    def test_batch_directory_with_images(self, tmp_path):
        """目录内 jpg/png/webp 被逐个处理；非图片扩展名被忽略"""
        _make_jpeg(tmp_path / "a.jpg")
        _make_jpeg(tmp_path / "b.png")
        (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
        result = runner.invoke(cli_mod.app, ["batch", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "找到 2 张图片" in result.output
        assert "处理: a.jpg" in result.output
        assert "处理: b.png" in result.output
        assert result.output.count("[Done] session_id=") == 2


class TestOtherCommands:
    def test_agents_list(self):
        result = runner.invoke(cli_mod.app, ["agents-list"])
        assert result.exit_code == 0
        assert "[Agent] 商品分析员" in result.output
        assert "requires:" in result.output
    def test_config_validate_ok(self):
        result = runner.invoke(cli_mod.app, ["config-validate"])
        assert result.exit_code == 0, result.output
        assert "[OK] All " in result.output
        assert "config files validated" in result.output

    def test_config_validate_error_path(self, monkeypatch):
        """配置加载失败 → 汇总报错并 exit 1（不抛裸异常）"""
        import src.core.config as cfg_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("模拟 YAML 损坏")

        monkeypatch.setattr(cfg_mod, "load_default_config", _boom)
        result = runner.invoke(cli_mod.app, ["config-validate"])
        assert result.exit_code == 1
        assert "[ERROR] 1 errors:" in result.output
        assert "config/default.yaml" in result.output


class TestAbTestCostDisplay:
    """A/B 排名行的金额：`cost_usd` 可能是 None（该变体模型价格未标定）

    此前是 `f"${r['cost_usd']:.4f}"` —— None 直接格式化崩溃；写成 `or 0.0` 又会把
    "不知道"显示成 $0.0000（用户质疑的误导）。现在未知显示 `—（未标定）`。
    """

    def _invoke_with_ab_ranking(self, tmp_path, monkeypatch, ranking):
        from src.chat.engine import ChatEngine

        async def _fake_run(self, session, **kwargs):
            session["artifacts"]["ab_test"] = {
                "agent_name": "提示词生成员",
                "winner": "v1",
                "winner_score": 88,
                "ranking": ranking,
            }
            session["status"] = "completed"
            return session

        monkeypatch.setattr(ChatEngine, "run", _fake_run)
        img = _make_jpeg(tmp_path / "ab.jpg")
        return runner.invoke(cli_mod.app, ["run", str(img), "--mode", "ab_test"])

    def test_priced_variant_shows_amount(self, tmp_path, monkeypatch):
        result = self._invoke_with_ab_ranking(tmp_path, monkeypatch, [
            {"id": "v1", "label": "gpt-4o", "score": 88, "cost_usd": 0.0123},
        ])
        assert result.exit_code == 0, result.output
        assert "v1 (gpt-4o): 88/100, $0.0123" in result.output

    def test_unpriced_variant_shows_dash_not_zero(self, tmp_path, monkeypatch):
        result = self._invoke_with_ab_ranking(tmp_path, monkeypatch, [
            {"id": "v1", "label": "方舟", "score": 88, "cost_usd": None},
            {"id": "v2", "label": "dall-e-3", "score": 70, "cost_usd": 0.04},
        ])
        assert result.exit_code == 0, result.output
        assert "v1 (方舟): 88/100, —（未标定）" in result.output
        assert "$0.0000" not in result.output, "未标定不得显示成 0"
        assert "v2 (dall-e-3): 70/100, $0.0400" in result.output

    def test_missing_cost_key_does_not_crash(self, tmp_path, monkeypatch):
        """老产物里根本没有 cost_usd 键 → 也要显示未标定而不是 KeyError"""
        result = self._invoke_with_ab_ranking(tmp_path, monkeypatch, [
            {"id": "v1", "label": "旧产物", "score": 60},
        ])
        assert result.exit_code == 0, result.output
        assert "—（未标定）" in result.output
