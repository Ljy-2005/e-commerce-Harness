"""一键启动器 start.py 单元测试（仅测纯探测函数，不真正启动服务）"""

import socket

import start


class TestPortInUse:
    def test_free_port_detected_as_free(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        assert start.port_in_use(port) is False

    def test_listening_port_detected(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            assert start.port_in_use(port) is True


class TestHttpGet:
    def test_connection_refused_returns_none(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        assert start.http_get(f"http://127.0.0.1:{port}", timeout=0.5) is None

    def test_backend_ready_false_when_down(self, monkeypatch):
        monkeypatch.setattr(start, "http_get", lambda url, timeout=1.5: None)
        assert start.backend_ready(8000) is False

    def test_backend_ready_true_on_200(self, monkeypatch):
        monkeypatch.setattr(start, "http_get", lambda url, timeout=1.5: (200, '{"status":"healthy"}'))
        assert start.backend_ready(8000) is True

    def test_frontend_ready_uses_localhost(self, monkeypatch):
        """前端就绪探测必须走 localhost（Vite 可能只绑定 IPv6 ::1）"""
        calls = []
        monkeypatch.setattr(start, "http_get", lambda url, timeout=1.5: calls.append(url) or (200, "ok"))
        assert start.frontend_ready(5173) is True
        assert calls[0].startswith("http://localhost:5173")
