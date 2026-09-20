"""setuptools 钩子：安装完成后自动装好敏感内容防护 git 钩子。

项目配置以 pyproject.toml 为准，本文件只负责补一个 cmdclass。
这样 `pip install -e ".[dev]"`（README 里让新环境跑的安装命令）执行完，
.git/hooks/ 里就已经有 pre-commit / pre-push 了，不需要额外记得跑脚本。

设计原则：**任何异常都不能让 pip install 失败**，最差只是没装钩子，
届时 CI 的 setup_hooks.py --check 会报出来。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parent


def _install_secret_hooks() -> None:
    script = ROOT / "scripts" / "setup_hooks.py"
    if not script.is_file() or not (ROOT / ".git").exists():
        return  # 从 sdist 安装、或不是 git 检出，跳过
    try:
        subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            check=False,
            timeout=60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


try:
    from setuptools.command.develop import develop as _develop
    from setuptools.command.install import install as _install

    class DevelopThenHooks(_develop):
        def run(self) -> None:
            super().run()
            _install_secret_hooks()

    class InstallThenHooks(_install):
        def run(self) -> None:
            super().run()
            _install_secret_hooks()

    _CMDCLASS = {"develop": DevelopThenHooks, "install": InstallThenHooks}
except Exception:  # pragma: no cover
    _CMDCLASS = {}


setup(cmdclass=_CMDCLASS)
