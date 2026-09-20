"""参考图治理 —— "文+图"里的"图"必须真的能送上去

用户要求："要保证在生图的过程中要文＋图生图"。这里钉住参考图的处理：
上传原图（可能是 PNG 大图）→ 嗅探 MIME → 超限降采样 → data URI，且张数有上限、
失败有说明（不静默丢）。
"""

import base64
import io

import pytest
from PIL import Image

from src.harness.reference_images import (
    MAX_REFERENCE_BYTES,
    collect_references,
    reference_sources,
    to_data_uri,
)


def _image_b64(fmt="PNG", size=(64, 64), color=(180, 40, 40)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


class TestToDataUri:
    def test_bare_base64_png(self):
        uri, note = to_data_uri(_image_b64())
        assert uri.startswith("data:image/png;base64,")
        assert note == ""

    def test_data_uri_passthrough(self):
        uri, _ = to_data_uri(f"data:image/jpeg;base64,{_image_b64('JPEG')}")
        assert uri.startswith("data:image/jpeg;base64,")

    def test_jpeg_sniffed(self):
        uri, _ = to_data_uri(_image_b64("JPEG"))
        assert uri.startswith("data:image/jpeg;base64,")

    @pytest.mark.parametrize("bad", ["", None, "not-base64!!!"])
    def test_garbage_returns_empty_uri_with_reason(self, bad):
        uri, note = to_data_uri(bad)
        assert uri == ""
        assert note

    def test_oversized_image_is_shrunk(self):
        """实测上传原图是 2048×2048 PNG 2.6MB → data URI 约 3.5MB，必须先压"""

        # 造一张噪声图（纯色图压缩后很小，测不出效果）
        import random
        random.seed(7)
        image = Image.new("RGB", (1600, 1600))
        image.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                       for _ in range(1600 * 1600)])
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        big = base64.b64encode(buf.getvalue()).decode()
        assert len(buf.getvalue()) > MAX_REFERENCE_BYTES       # 前提：确实超限

        uri, note = to_data_uri(big)
        assert uri.startswith("data:image/jpeg;base64,")
        assert "压缩" in note
        payload = base64.b64decode(uri.partition(",")[2])
        assert len(payload) < len(buf.getvalue())

    def test_small_image_not_touched(self):
        raw = base64.b64decode(_image_b64())
        uri, note = to_data_uri(_image_b64())
        assert note == ""
        assert base64.b64decode(uri.partition(",")[2]) == raw


class TestCollectReferences:
    def test_order_preserved(self):
        first, second = _image_b64(color=(10, 10, 10)), _image_b64(color=(250, 250, 250))
        uris, notes = collect_references([first, second])
        assert len(uris) == 2
        assert base64.b64decode(uris[0].partition(",")[2]) != base64.b64decode(uris[1].partition(",")[2])
        assert notes == []

    def test_limit_and_note(self):
        uris, notes = collect_references([_image_b64()] * 6, limit=4)
        assert len(uris) == 4
        assert any("仅取前 4 张" in note for note in notes)

    def test_failure_does_not_drop_the_rest(self):
        uris, notes = collect_references([_image_b64(), "!!!bad!!!", _image_b64()])
        assert len(uris) == 2
        assert any("图2" in note for note in notes)

    def test_empty_sources(self):
        uris, notes = collect_references([])
        assert uris == []
        assert notes and "没有可用的上传图" in notes[0]

    def test_non_list_input(self):
        uris, notes = collect_references(None)
        assert uris == [] and notes


class TestReferenceSources:
    def test_product_images_first(self):
        session = {"task": {"product_images": ["a"], "reference_images": ["b"]}}
        assert reference_sources(session) == ["a", "b"]

    def test_garbage_session(self):
        assert reference_sources({}) == []
        assert reference_sources(None) == []
        assert reference_sources({"task": {"product_images": "not-a-list"}}) == []
