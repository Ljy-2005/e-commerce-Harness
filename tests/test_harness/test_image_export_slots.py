"""生成图落盘命名 —— 带槽位，且不破坏按序号查找

用户要求交付物是"可直接上传的套图"：文件按平台槽位命名（`main_white` 等）后，
导出的 ZIP 一眼能对上平台要求的槽位顺序。

约束：序号必须仍在**最后** —— `find_session_file()` 按 `_{index}` 结尾匹配，
下载/查找端点靠它工作，加槽位不能把它弄坏。
"""

import base64
import io

import pytest
from PIL import Image

from src.storage import image_export as ex
from src.storage.image_export import build_image_path, find_session_file, save_images


def _b64(color=(10, 20, 30)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class TestBuildImagePath:
    def test_slot_in_name_and_index_last(self):
        path = build_image_path("default", "sess1", "taobao", "保健品", 3, ".jpg",
                                slot_id="main_white")
        assert path.name == "taobao_保健品_main_white_3.jpg"

    def test_without_slot_keeps_legacy_name(self):
        path = build_image_path("default", "sess1", "taobao", "保健品", 2, ".png")
        assert path.name == "taobao_保健品_2.png"

    def test_slot_is_sanitized(self):
        path = build_image_path("default", "sess1", "pdd", "食品", 1, ".png",
                                slot_id="../../evil slot")
        assert path.parent.name == "sess1"
        assert ".." not in path.name and "/" not in path.name


class TestSaveImagesWithSlots:
    @pytest.mark.asyncio
    async def test_slot_named_files_and_index_lookup(self):
        images = [
            {"base64_data": _b64(), "slot_id": "main_white"},
            {"base64_data": _b64((200, 10, 10)), "slot_id": "main_scene"},
        ]
        results = await save_images("sess-slots", "default", "taobao", "保健品", images)
        names = [ex.Path(r["path"]).name for r in results]
        assert names == ["taobao_保健品_main_white_1.png", "taobao_保健品_main_scene_2.png"]

        # 关键：加槽位后按序号查找仍然工作（下载端点依赖它）
        assert find_session_file("default", "sess-slots", 2).name.endswith("main_scene_2.png")
        assert find_session_file("default", "sess-slots", 1).name.endswith("main_white_1.png")

    @pytest.mark.asyncio
    async def test_legacy_images_without_slot(self):
        results = await save_images("sess-legacy", "default", "taobao", "保健品",
                                    [{"base64_data": _b64()}])
        assert ex.Path(results[0]["path"]).name == "taobao_保健品_1.png"
