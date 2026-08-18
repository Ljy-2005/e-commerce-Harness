"""向后兼容入口（实现已迁至 src/api/main.py，PRD D4 分层）

真正实现位于 `src/api/main.py`。保留本文件仅为兼容既有 `uvicorn src.main:app`
启动命令与 `import src.main` 的旧路径 —— 通过 `sys.modules` 别名，所有属性
读写都与 `src.api.main` 共享同一模块命名空间（测试中
`main_mod._provider_registry = ...` 等模块属性突变依然生效）。

新代码请使用 `from src.api.main import app`。
"""

import sys

from src.api import main as _impl

sys.modules[__name__] = _impl
