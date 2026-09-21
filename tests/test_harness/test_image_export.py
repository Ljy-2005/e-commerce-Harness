"""生成图片导出（B3-27 / 用户反馈「没法设置导出路径」）回归测试。

覆盖：输出根解析优先级（env → config/output.yaml → ./output）、目录布局与命名净化、
三种图像来源（内联 base64 / data URI / 远程 URL）、容错（坏记录不影响其余）、
按序查找、ZIP 打包、租户隔离与路径穿越防护。
"""

import base64
import io
import zipfile

import pytest

import src.core.config as config_mod
from src.storage import image_export as ex

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"></svg>'


@pytest.fixture(autouse=True)
def _output_root(tmp_path, monkeypatch):
    """输出根指向 tmp（同时也验证 ECOMM_OUTPUT_DIR 生效）"""
    root = tmp_path / "out"
    monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(root))
    monkeypatch.setattr(config_mod, "OUTPUT_CONFIG_REL", str(tmp_path / "output.yaml"))
    return root


class TestOutputRootResolution:
    def test_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path / "from_env"))
        config_mod.save_output_dir(str(tmp_path / "from_file"))

        assert config_mod.output_root() == tmp_path / "from_env"
        assert config_mod.output_dir_source() == "env"

    def test_file_then_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ECOMM_OUTPUT_DIR", raising=False)
        assert config_mod.output_dir_source() == "default"
        assert config_mod.output_root() == config_mod._project_root() / "output"

        config_mod.save_output_dir(str(tmp_path / "from_file"))
        assert config_mod.output_root() == tmp_path / "from_file"
        assert config_mod.output_dir_source() == "file"

    def test_relative_path_resolves_against_project_root(self, monkeypatch):
        monkeypatch.delenv("ECOMM_OUTPUT_DIR", raising=False)
        config_mod.save_output_dir("exports/2026")

        assert config_mod.output_root() == config_mod._project_root() / "exports" / "2026"

    def test_status_reports_writable_and_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path / "ok"))
        status = config_mod.output_status()
        assert status["writable"] is True and status["error"] == ""
        assert status["env_locked"] is True

        # 用一个"父路径是文件"的非法路径制造 mkdir 失败
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(blocker / "sub"))
        bad = config_mod.output_status()
        assert bad["writable"] is False and bad["error"]


class TestLayout:
    def test_detect_extension(self):
        assert ex.detect_extension(PNG) == ".png"
        assert ex.detect_extension(JPEG) == ".jpg"
        assert ex.detect_extension(SVG) == ".svg"
        assert ex.detect_extension(b"garbage") == ".bin"

    def test_path_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path))
        path = ex.build_image_path("brand_a", "sess123", "taobao", "保健品", 2, ".png")

        assert path == tmp_path / "brand_a" / "sess123" / "taobao_保健品_2.png"

    def test_path_sanitization_blocks_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path))
        path = ex.build_image_path("../../etc", "..\\..\\win", "../../x", "a/b:c*d", 1)

        assert path.parent.parent.parent == tmp_path, "不得逃出输出根"
        assert ".." not in path.name and "/" not in path.name and "\\" not in path.name

    def test_missing_parts_have_fallbacks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ECOMM_OUTPUT_DIR", str(tmp_path))
        path = ex.build_image_path("", "", "", "", 1)
        assert path.name == "na_未分类_1.png"
        assert path.parent.name == "session"


class TestSaveImages:
    @pytest.mark.asyncio
    async def test_saves_inline_base64_with_detected_ext(self, _output_root):
        results = await ex.save_images("sess1", "t1", "taobao", "保健品", [
            {"base64_data": base64.b64encode(PNG).decode()},
            {"base64_data": base64.b64encode(SVG).decode()},
        ])

        assert [r["ok"] for r in results] == [True, True]
        assert results[0]["rel_path"] == "t1/sess1/taobao_保健品_1.png"   # POSIX 风格
        assert results[1]["rel_path"].endswith("taobao_保健品_2.svg")
        written = _output_root / "t1" / "sess1" / "taobao_保健品_1.png"
        assert written.read_bytes() == PNG
        assert results[0]["bytes"] == len(PNG)

    @pytest.mark.asyncio
    async def test_saves_data_uri(self, _output_root):
        uri = "data:image/png;base64," + base64.b64encode(PNG).decode()

        results = await ex.save_images("sess1", "t1", "amazon", "食品",
                                       [{"image_url": uri, "base64_data": ""}])

        assert results[0]["ok"] is True
        assert (_output_root / "t1" / "sess1" / "amazon_食品_1.png").read_bytes() == PNG

    @pytest.mark.asyncio
    async def test_downloads_remote_url(self, _output_root, monkeypatch):
        async def _fake_fetch(url):
            assert url == "https://cdn.example/a.jpg"
            return JPEG

        monkeypatch.setattr(ex, "_fetch_remote", _fake_fetch)

        results = await ex.save_images("sess1", "t1", "jd", "3C数码",
                                       [{"image_url": "https://cdn.example/a.jpg"}])

        assert results[0]["ok"] is True
        assert (_output_root / "t1" / "sess1" / "jd_3C数码_1.jpg").read_bytes() == JPEG

    @pytest.mark.asyncio
    async def test_bad_record_does_not_block_others(self, _output_root):
        results = await ex.save_images("sess1", "t1", "taobao", "食品", [
            {"base64_data": ""},
            "not-a-dict",
            {"base64_data": base64.b64encode(PNG).decode()},
        ])

        assert [r["ok"] for r in results] == [False, False, True]
        assert all(r["error"] for r in results[:2])
        assert results[2]["index"] == 3, "序号按列表位置（失败项也占位，保证可预期）"

    @pytest.mark.asyncio
    async def test_media_type_prefix_is_ignored(self, _output_root):
        """前端传 `data:image/png;base64,` 前缀时 base64_data 不带前缀（回归）"""
        results = await ex.save_images("s", "t", "p", "c",
                                       [{"base64_data": base64.b64encode(JPEG).decode()}])
        assert results[0]["rel_path"].endswith(".jpg")


class TestLookupAndZip:
    @pytest.mark.asyncio
    async def test_list_and_find_by_index(self, _output_root):
        await ex.save_images("sess1", "t1", "taobao", "保健品", [
            {"base64_data": base64.b64encode(PNG).decode()},
            {"base64_data": base64.b64encode(JPEG).decode()},
        ])

        files = ex.list_session_files("t1", "sess1")
        assert len(files) == 2
        assert ex.find_session_file("t1", "sess1", 2).name == "taobao_保健品_2.jpg"
        assert ex.find_session_file("t1", "sess1", 9) is None

    @pytest.mark.asyncio
    async def test_builds_zip(self, _output_root):
        await ex.save_images("sess1", "t1", "taobao", "保健品", [
            {"base64_data": base64.b64encode(PNG).decode()},
            {"base64_data": base64.b64encode(JPEG).decode()},
        ])

        payload = ex.build_session_zip("t1", "sess1")

        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = sorted(zf.namelist())
            assert names == ["taobao_保健品_1.png", "taobao_保健品_2.jpg"]
            assert zf.read("taobao_保健品_1.png") == PNG

    def test_zip_is_none_without_files(self, _output_root):
        assert ex.build_session_zip("t1", "nothing") is None


class TestIsolation:
    @pytest.mark.asyncio
    async def test_tenants_and_sessions_do_not_share_dirs(self, _output_root):
        image = [{"base64_data": base64.b64encode(PNG).decode()}]
        await ex.save_images("sess1", "t1", "taobao", "食品", image)
        await ex.save_images("sess1", "t2", "taobao", "食品", image)
        await ex.save_images("sess2", "t1", "taobao", "食品", image)

        assert len(ex.list_session_files("t1", "sess1")) == 1
        assert len(ex.list_session_files("t2", "sess1")) == 1
        assert len(ex.list_session_files("t1", "sess2")) == 1
        assert ex.list_session_files("t1", "sess1") != ex.list_session_files("t2", "sess1")
