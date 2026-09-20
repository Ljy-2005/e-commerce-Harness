"""vision_payload 单元测试（A31）

实测事故：审查员/合规审查员只认 `base64_data`，而真实生图 Provider（方舟
Seedream / DALL·E / FLUX）全部只返回 `image_url` → 审查员永远 `NO_IMAGE_ACCESSIBLE`。
"""

import base64
from pathlib import Path

import pytest

from src.harness.vision_payload import (
    MAX_BYTES, image_parts, sniff_mime,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


class TestSniffMime:
    def test_known_formats(self):
        assert sniff_mime(PNG) == "image/png"
        assert sniff_mime(JPEG) == "image/jpeg"
        assert sniff_mime(b"GIF89a" + b"\x00" * 10) == "image/gif"
        assert sniff_mime(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8) == "image/webp"
        assert sniff_mime(b"<svg xmlns='...'></svg>") == "image/svg+xml"

    def test_unknown_defaults_to_png(self):
        assert sniff_mime(b"") == "image/png"
        assert sniff_mime(b"\x00\x01\x02\x03") == "image/png"


class TestImageParts:
    @pytest.mark.asyncio
    async def test_base64_is_sniffed_not_assumed_png(self):
        """方舟返回的是 jpeg：写死 png 的 data URI 会让部分模型拒绝"""
        parts, notes = await image_parts([{"base64_data": _b64(JPEG)}])
        assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert notes == []

    @pytest.mark.asyncio
    async def test_data_uri_passthrough(self):
        uri = f"data:image/webp;base64,{_b64(PNG)}"
        parts, _ = await image_parts([{"image_url": uri}])
        assert parts[0]["image_url"]["url"] == uri

    @pytest.mark.asyncio
    async def test_local_saved_path_preferred(self, tmp_path):
        """落盘文件是首选来源（引擎生成后已自动落盘）"""
        (tmp_path / "default" / "s1").mkdir(parents=True)
        (tmp_path / "default" / "s1" / "a.jpg").write_bytes(JPEG)
        parts, notes = await image_parts(
            [{"saved_path": "default/s1/a.jpg", "image_url": "https://cdn/x.jpg"}],
            output_root=tmp_path,
        )
        assert parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        assert notes == []

    @pytest.mark.asyncio
    async def test_saved_path_traversal_rejected(self, tmp_path):
        """saved_path 来自产物字段：越界必须拒绝（不能读宿主机任意文件）"""
        secret = tmp_path.parent / "secret.png"
        secret.write_bytes(PNG)
        parts, notes = await image_parts(
            [{"saved_path": "../secret.png"}], output_root=tmp_path / "out"
        )
        assert parts == []
        assert notes and "落盘路径不可用" in notes[0]

    @pytest.mark.asyncio
    async def test_remote_url_downloaded(self):
        calls = []

        async def fake_download(url):
            calls.append(url)
            return PNG

        parts, notes = await image_parts(
            [{"prompt_name": "variant_1", "image_url": "https://cdn.example/a.png"}],
            downloader=fake_download,
        )
        assert calls == ["https://cdn.example/a.png"]
        assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert notes == []

    @pytest.mark.asyncio
    async def test_download_failure_is_noted_not_raised(self):
        async def boom(url):
            raise RuntimeError("HTTP 403")

        parts, notes = await image_parts(
            [{"prompt_name": "variant_1", "image_url": "https://cdn.example/a.png"}],
            downloader=boom,
        )
        assert parts == []
        assert "variant_1" in notes[0] and "403" in notes[0]

    @pytest.mark.asyncio
    async def test_no_source_reports_reason(self):
        parts, notes = await image_parts([{"prompt_name": "variant_1"}])
        assert parts == []
        assert "没有可用的图像数据" in notes[0]

    @pytest.mark.asyncio
    async def test_empty_and_non_list_inputs(self):
        assert await image_parts([]) == ([], [])
        parts, notes = await image_parts(None)
        assert parts == [] and notes

    @pytest.mark.asyncio
    async def test_limit_is_reported(self):
        images = [{"base64_data": _b64(PNG)}] * 5
        parts, notes = await image_parts(images, limit=3)
        assert len(parts) == 3
        assert any("仅取前 3 张" in n for n in notes)

    @pytest.mark.asyncio
    async def test_oversize_local_file_rejected(self, tmp_path):
        big = tmp_path / "big.png"
        big.write_bytes(PNG + b"\x00" * (MAX_BYTES + 1))
        parts, notes = await image_parts([{"saved_path": "big.png"}], output_root=tmp_path)
        assert parts == []
        assert any("过大" in n for n in notes)
