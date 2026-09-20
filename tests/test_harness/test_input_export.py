"""上传原图落盘（inputs/）—— 让"文+图"的"图"在重启后依然拿得到

为什么必须落盘：轻量快照会剔除 `task.product_images`（base64 太占空间），崩溃恢复/重启后
内存里没有上传图 → 参考图为空 → i2i **静默退化成纯文生图**，又回到"模型编造包装文字"。
"""

import base64
import io

import pytest
from PIL import Image

from src.harness.reference_images import reference_sources
from src.storage import image_export as ex


def _b64(size=(20, 20), color=(9, 9, 9), fmt="PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


class TestSaveInputs:
    @pytest.mark.asyncio
    async def test_writes_under_inputs_with_metadata(self):
        items = await ex.save_inputs("default", "sess-in", [_b64(), _b64(fmt="JPEG")])
        assert [item["slot"] for item in items] == ["upload_1", "upload_2"]
        assert all(item["ok"] for item in items)
        assert items[0]["rel_path"].endswith("inputs/upload_1.png")
        assert items[1]["rel_path"].endswith("inputs/upload_2.jpg")
        assert items[0]["mime"] == "image/png"
        assert items[1]["mime"] == "image/jpeg"
        assert (ex.output_root() / items[0]["rel_path"]).exists()

    @pytest.mark.asyncio
    async def test_bad_entry_does_not_break_others(self):
        items = await ex.save_inputs("default", "sess-bad", [_b64(), "!!!not-base64!!!", _b64()])
        assert [item["ok"] for item in items] == [True, False, True]
        assert items[1]["error"]

    @pytest.mark.asyncio
    async def test_accepts_data_uri_and_dict(self):
        items = await ex.save_inputs("default", "sess-uri", [
            f"data:image/png;base64,{_b64()}",
            {"base64_data": _b64()},
        ])
        assert all(item["ok"] for item in items)


class TestLoadInputs:
    @pytest.mark.asyncio
    async def test_roundtrip_ordered(self):
        await ex.save_inputs("default", "sess-rt", [_b64(color=(1, 1, 1)), _b64(color=(2, 2, 2))])
        loaded = ex.load_inputs("default", "sess-rt")
        assert [item["slot"] for item in loaded] == ["upload_1", "upload_2"]
        assert loaded[0]["data"] != loaded[1]["data"]

    def test_missing_dir(self):
        assert ex.load_inputs("default", "nope") == []


class TestReferenceFallback:
    @pytest.mark.asyncio
    async def test_reference_sources_fall_back_to_disk(self):
        """模拟"轻量快照后重启"：会话里没有 product_images，但磁盘上有 inputs/"""
        await ex.save_inputs("default", "sess-fb", [_b64()])
        session = {"session_id": "sess-fb", "tenant_id": "default",
                   "task": {"product_images": [], "platform": "taobao"}}
        sources = reference_sources(session)
        assert len(sources) == 1
        assert base64.b64decode(sources[0])[:8] == b"\x89PNG\r\n\x1a\n"

    @pytest.mark.asyncio
    async def test_in_memory_wins_over_disk(self):
        await ex.save_inputs("default", "sess-win", [_b64()])
        session = {"session_id": "sess-win", "tenant_id": "default",
                   "task": {"product_images": ["IN-MEMORY"]}}
        assert reference_sources(session) == ["IN-MEMORY"]

    def test_no_session_id(self):
        assert reference_sources({"task": {}}) == []


class TestReadOutputFile:
    @pytest.mark.asyncio
    async def test_reads_own_output(self):
        items = await ex.save_inputs("default", "sess-read", [_b64()])
        assert ex.read_output_file(items[0]["rel_path"])

    @pytest.mark.parametrize("bad", ["", None, "../../secrets.yaml", "..\\..\\config\\default.yaml"])
    def test_traversal_guarded(self, bad):
        assert ex.read_output_file(bad) is None
