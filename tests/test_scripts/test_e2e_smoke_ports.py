"""第三轮审计 B2-17：E2E 冒烟脚本的端口语义回归测试。

此前 `_stop_children` 无条件 `_kill_port_listeners([8000, 5173])`：
- `--existing`（文档承诺"复用已在运行的服务"）跑完也会把用户自己的前后端杀掉；
- 自动模式不预检端口，端口被占用时健康检查会命中**别人**的服务 → 误判"就绪"，
  实际在验证另一个实例，收尾还会杀掉它。
"""

import importlib.util
import os
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location("e2e_smoke_under_test",
                                                  ROOT / "scripts" / "e2e_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


es = _load_module()


class TestChildCleanupScope:
    """收尾只清理本次自己启动的端口"""

    def test_existing_mode_kills_nothing(self, monkeypatch):
        killed: list = []
        monkeypatch.setattr(es, "_kill_port_listeners", lambda ports: killed.append(list(ports)))

        es._stop_children([], [])   # --existing：started 与 owned_ports 均为空

        assert killed == [], "复用模式不得清理任何端口/进程"

    @pytest.mark.skipif(os.name != "nt", reason="按端口清扫仅 Windows 路径启用")
    def test_only_owned_ports_are_reaped(self, monkeypatch):
        killed: list = []
        monkeypatch.setattr(es, "_kill_port_listeners", lambda ports: killed.append(list(ports)))

        es._stop_children([], [8123])

        assert killed == [[8123]], "只应清扫本次启动的端口"


class TestAutoModePrecheck:
    """自动启动模式：端口被占用或自定义端口时明确拒绝"""

    def test_default_free_ports_pass(self):
        assert es._auto_mode_conflict(
            "http://127.0.0.1:8000", "http://localhost:5173", probe=lambda _p: False) == ""

    def test_occupied_default_port_rejected(self):
        msg = es._auto_mode_conflict(
            "http://127.0.0.1:8000", "http://localhost:5173", probe=lambda p: p == 8000)

        assert msg, "端口被占用时必须拒绝自动启动"
        assert "8000" in msg and "占用" in msg
        assert "--existing" in msg, "拒绝信息需给出可执行的替代路径"

    def test_custom_port_requires_existing_mode(self):
        msg = es._auto_mode_conflict(
            "http://127.0.0.1:9000", "http://localhost:5173", probe=lambda _p: False)

        assert msg, "自定义端口在自动模式下会与 vite 代理（硬编码 8000）错配，必须拒绝"
        assert "9000" in msg and "--existing" in msg


class TestPortProbe:
    def test_detects_real_listener_then_free(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            assert es._port_in_use(port) is True

        assert es._port_in_use(port) is False, "监听关闭后应判定为空闲"

    def test_url_port_defaults(self):
        assert es._url_port("http://127.0.0.1:8001", 8000) == 8001
        assert es._url_port("https://example.com", 8000) == 443
        assert es._url_port("http://127.0.0.1", 8000) == 80
