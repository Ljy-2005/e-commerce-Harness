"""E-Commerce Harness 一键启动器

用法：
    python start.py                  # 启动后端 + 前端，就绪后自动打开浏览器
    python start.py --no-browser     # 启动但不自动打开浏览器
    python start.py --backend-only   # 只启动后端 API（8000）
    python start.py --frontend-only  # 只启动前端（5173）
    python start.py --backend-port 8001 --frontend-port 5174   # 自定义端口

行为：
- 若服务已在运行（端口被占用且健康检查通过），复用现有服务，不重复启动
- 就绪前显示等待进度；就绪后打印访问地址并自动打开浏览器
- 按 Ctrl+C 停止本次启动的全部子进程
- 前端未安装依赖 / 后端缺依赖时会给出明确提示

Windows 用户可直接双击 start.bat；停止服务用 stop.bat。
"""

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
HEALTH_TIMEOUT_S = 90

# ANSI 颜色（Windows 10+ 终端支持；set_ansi 开启 VT 处理）
C = {"ok": "\033[92m", "warn": "\033[93m", "err": "\033[91m", "dim": "\033[90m", "bold": "\033[1m", "reset": "\033[0m"}


def _setup_console() -> None:
    """UTF-8 输出 + Windows 启用 ANSI 转义"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    if os.name == "nt":
        os.system("")  # 激活 Windows 控制台 VT 处理


def _log(msg: str, color: str = "") -> None:
    print(f"{C.get(color, '')}{msg}{C['reset']}", flush=True)


# ── 端口与就绪探测 ──


def port_in_use(port: int) -> bool:
    """检查本机端口是否已被监听（覆盖 IPv4/IPv6）"""
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except OSError:
            continue
    return False


def http_get(url: str, timeout: float = 2.0) -> tuple[int, str] | None:
    """GET 请求，返回 (状态码, 文本) 或 None（连接失败）"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return (resp.status, resp.read(200).decode("utf-8", errors="replace"))
    except Exception:
        return None


def backend_ready(port: int) -> bool:
    res = http_get(f"http://127.0.0.1:{port}/health", timeout=1.5)
    return bool(res and res[0] == 200)


def frontend_ready(port: int) -> bool:
    # Vite 默认绑定 localhost（可能仅 IPv6 ::1），必须用 localhost 探测
    res = http_get(f"http://localhost:{port}", timeout=1.5)
    return bool(res and res[0] == 200)


def wait_until(check, label: str, timeout_s: int = HEALTH_TIMEOUT_S) -> bool:
    """轮询等待 check() 为真，打印进度点"""
    _log(f"⏳ 等待{label}就绪", "dim")
    deadline = time.monotonic() + timeout_s
    dots = 0
    while time.monotonic() < deadline:
        if check():
            _log(f"✅ {label}就绪")
            return True
        dots += 1
        sys.stdout.write("." if dots % 10 else ".\n")
        sys.stdout.flush()
        time.sleep(0.5)
    _log(f"\n❌ 等待{label}超时（{timeout_s}s）", "err")
    return False


# ── 启动子进程 ──


def start_backend(port: int) -> subprocess.Popen:
    _log(f"🚀 启动后端 uvicorn → http://127.0.0.1:{port}")
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.api.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT_ROOT,
        env=os.environ.copy(),
    )


def start_frontend(port: int) -> subprocess.Popen:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    _log(f"🚀 启动前端 Vite → http://localhost:{port}")
    return subprocess.Popen(
        [npm, "run", "dev", "--", "--port", str(port), "--host", "localhost"],
        cwd=FRONTEND_DIR,
        env=os.environ.copy(),
    )


# ── 依赖预检 ──


def check_deps() -> bool:
    ok = True
    if not (FRONTEND_DIR / "node_modules").exists():
        _log("❌ 前端依赖未安装：请先执行  cd frontend && npm install", "err")
        ok = False
    try:
        subprocess.run(
            [sys.executable, "-c", "import fastapi, uvicorn"],
            cwd=PROJECT_ROOT, check=True, capture_output=True, timeout=30,
        )
    except Exception:
        _log("❌ 后端依赖缺失：请先执行  pip install -e \".[dev]\"", "err")
        ok = False
    return ok


# ── 主流程 ──


def main() -> int:
    _setup_console()
    parser = argparse.ArgumentParser(description="E-Commerce Harness 一键启动")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--backend-only", action="store_true", help="只启动后端")
    parser.add_argument("--frontend-only", action="store_true", help="只启动前端")
    parser.add_argument("--backend-port", type=int, default=BACKEND_PORT, help=f"后端端口（默认 {BACKEND_PORT}）")
    parser.add_argument("--frontend-port", type=int, default=FRONTEND_PORT, help=f"前端端口（默认 {FRONTEND_PORT}）")
    args = parser.parse_args()

    want_backend = not args.frontend_only
    want_frontend = not args.backend_only
    bport, fport = args.backend_port, args.frontend_port

    _log(f"\n{C['bold']}E-Commerce Harness — 一键启动{C['reset']}")
    _log(f"{C['dim']}项目目录: {PROJECT_ROOT}{C['reset']}")

    if not check_deps():
        return 1

    procs: list[subprocess.Popen] = []
    reuse_backend = reuse_frontend = False

    # 后端
    if want_backend:
        if port_in_use(bport):
            if backend_ready(bport):
                _log(f"ℹ 后端已在运行（端口 {bport} 健康检查通过），复用现有服务", "dim")
                reuse_backend = True
            else:
                _log(f"❌ 端口 {bport} 被占用且不是本项目后端，请先停止占用进程（stop.bat）或换端口", "err")
                return 1
        else:
            procs.append(start_backend(bport))
            if not wait_until(lambda: backend_ready(bport), f"后端（:{bport}）"):
                return 1

    # 前端
    if want_frontend:
        if port_in_use(fport):
            if frontend_ready(fport):
                _log(f"ℹ 前端已在运行（端口 {fport}），复用现有服务", "dim")
                reuse_frontend = True
            else:
                _log(f"❌ 端口 {fport} 被占用且不是本项目前端，请先停止占用进程（stop.bat）或换端口", "err")
                return 1
        else:
            procs.append(start_frontend(fport))
            if not wait_until(lambda: frontend_ready(fport), f"前端（:{fport}）"):
                return 1

    _log(f"\n{C['bold']}{C['ok']}🎉 全部就绪！{C['reset']}")
    if not args.backend_only:
        _log(f"  前端工作台: http://localhost:{fport}")
    if not args.frontend_only:
        _log(f"  后端 API   : http://127.0.0.1:{bport}  (文档 /docs)")
    _log(f"{C['dim']}  按 Ctrl+C 停止本次启动的服务{C['reset']}")

    if not args.backend_only and not args.no_browser:
        try:
            webbrowser.open(f"http://localhost:{fport}")
            _log("🌐 已打开浏览器")
        except Exception:
            _log("⚠ 自动打开浏览器失败，请手动访问上面的地址", "warn")

    # 常驻等待：子进程退出则提醒；Ctrl+C 停止全部
    try:
        while True:
            for p in list(procs):
                if p.poll() is not None:
                    _log(f"\n⚠ 子进程退出（code={p.returncode}），正在停止其余服务...", "warn")
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        _log("\n🛑 正在停止服务...", "dim")
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        _log("👋 已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
