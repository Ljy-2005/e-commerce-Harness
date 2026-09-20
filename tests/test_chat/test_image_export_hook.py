"""生成图自动落盘接线测试（用户反馈：没法设置导出路径）。

聊天引擎与工作流引擎都要把生成的图写到 `{输出根}/{租户}/{会话ID}/{平台}_{品类}_{序号}.ext`，
并把落盘相对路径回写到 image 记录（供界面显示）。
落盘失败只告警，绝不影响生成任务。
"""

import base64

import pytest

from src.agents.registry import AgentRegistry
from src.chat.engine import ChatEngine
from src.chat.session import SessionManager
from src.providers import get_provider_registry
from src.storage import image_export as ex


@pytest.fixture(autouse=True)
def _out_root(tmp_path, monkeypatch):
    root = tmp_path / "output"
    monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(root))
    return root


@pytest.fixture
async def engine():
    registry = AgentRegistry()
    await registry.load_from_config(get_provider_registry())
    mgr = SessionManager()
    return ChatEngine(registry=registry, session_manager=mgr), mgr


@pytest.mark.asyncio
async def test_chat_run_saves_images_and_records_path(engine, sample_image_base64, _out_root):
    eng, mgr = engine
    session = mgr.create(product_images=[sample_image_base64], product_info="护肝胶囊",
                         platform="taobao", category_hint="保健品")

    result = await eng.run(session)

    files = ex.list_session_files("default", result["session_id"])
    images = result["artifacts"]["images"]
    # 张数由 Mock 的套图编排决定（2 摄影槽位 + 3 信息图），不写死数字
    assert len(files) == len(images) == 5, f"应有 5 个槽位落盘，实际 {[f.name for f in files]}"
    suffixes = {f.suffix for f in files}
    assert suffixes == {".svg", ".jpg"}, f"摄影槽位是 SVG 占位图、信息图是本地排版 JPEG：{suffixes}"

    assert all(img.get("saved_path") for img in images), "落盘路径应回写到 image 记录"
    assert images[0]["saved_path"].startswith("default/"), "相对输出根、含租户与会话目录"
    for img, path in zip(images, files):
        assert (_out_root / img["saved_path"]).exists()


@pytest.mark.asyncio
async def test_chat_save_uses_platform_and_category_in_name(engine, sample_image_base64, _out_root,
                                                            monkeypatch):
    from src.providers import mock as mock_mod
    monkeypatch.setitem(mock_mod.MOCK_ANALYSIS, "category", "化妆品")
    eng, mgr = engine
    session = mgr.create(product_images=[sample_image_base64], product_info="测试",
                         platform="amazon", category_hint="化妆品")

    result = await eng.run(session)

    names = [p.name for p in ex.list_session_files("default", result["session_id"])]
    assert any(n.startswith("amazon_") for n in names), names
    # 品类取分析结果（流水线结论），而不是用户填的 category_hint
    assert any("化妆品" in n for n in names), names


@pytest.mark.asyncio
async def test_save_failure_does_not_break_run(engine, sample_image_base64, monkeypatch):
    """落盘异常（如只读盘）不得影响生成任务本身"""
    eng, mgr = engine
    session = mgr.create(product_images=[sample_image_base64], product_info="测试")

    async def _boom(*args, **kwargs):
        raise OSError("disk read-only")

    monkeypatch.setattr("src.storage.image_export.save_images", _boom)

    result = await eng.run(session)

    assert result["status"] == "completed"
    assert result["artifacts"]["images"], "产物仍然保留"


@pytest.mark.asyncio
async def test_tenant_isolation_in_output_dir(engine, sample_image_base64, _out_root):
    eng, mgr = engine
    session = mgr.create(product_images=[sample_image_base64], product_info="测试",
                         tenant_id="brand_a")

    await eng.run(session)

    assert ex.list_session_files("brand_a", session["session_id"]), "租户目录应按租户落地"
    assert ex.list_session_files("default", session["session_id"]) == []
