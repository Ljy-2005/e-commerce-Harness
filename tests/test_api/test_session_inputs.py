"""创建会话时把上传原图落盘（inputs/）

参考图（"文+图"里的"图"）以前只活在内存与完整 checkpoint 里；轻量快照会剔除 base64，
重启后参考图就没了 → i2i 静默退化成纯文生图。这里钉住创建会话时的落盘与元数据记录。
"""

import base64
import io

import pytest
from PIL import Image

import src.api.main as main_mod
from src.storage import image_export as ex


def _b64(size=(20, 20), color=(30, 60, 90)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture(autouse=True)
def _isolate_output(tmp_path, monkeypatch):
    monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path / "output"))


class TestPersistSessionInputs:
    @pytest.mark.asyncio
    async def test_saves_and_records_metadata(self):
        session = main_mod._session_manager.create([_b64(), _b64(color=(1, 2, 3))],
                                                   product_info="测试", tenant_id="default")
        saved = await main_mod._persist_session_inputs(session)

        assert [item["slot"] for item in saved] == ["upload_1", "upload_2"]
        assert session["task"]["input_files"][0]["rel_path"].endswith("inputs/upload_1.png")
        assert (ex.output_root() / saved[0]["rel_path"]).exists()
        assert saved[0]["mime"] == "image/png"

    @pytest.mark.asyncio
    async def test_no_images_is_noop(self):
        session = main_mod._session_manager.create([], product_info="测试")
        assert await main_mod._persist_session_inputs(session) == []
        assert "input_files" not in session["task"]

    @pytest.mark.asyncio
    async def test_failure_does_not_raise(self, monkeypatch):
        """落盘失败（只读盘等）不得影响会话创建"""
        async def _boom(*args, **kwargs):
            raise OSError("disk read-only")

        monkeypatch.setattr(ex, "save_inputs", _boom)
        session = main_mod._session_manager.create([_b64()], product_info="测试")
        assert await main_mod._persist_session_inputs(session) == []
