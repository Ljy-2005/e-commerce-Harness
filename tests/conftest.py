"""共享 fixtures"""

import asyncio
import os
import shutil
from pathlib import Path

import pytest
import base64


# ── P3 补缺：确定性 Mock 环境（test-plan §1.2「Mock First：无网、无 Key、确定性」）──
#
# P2 实测教训（见 progress.md 决策 36）：本地 config/secrets.yaml 持久化的
# Provider Key 会在 `src.core.config` import 时注入 os.environ —— 只设
# MOCK_MODE=true 不够，Agent 仍会走真实 API（慢、行为随机、可能产生费用）。
# 全量测试套件在会话开始前清空全部 Key + 强制 MOCK_MODE=true，并重建
# Provider/Agent 注册表（collection 期模块导入可能已带 Key 构建了旧注册表）。
# 需要 Key 的测试（鉴权矩阵/Provider 探测/租户 Key）用 monkeypatch 自行注入，
# 与本 fixture 不冲突（monkeypatch 每测试后还原到"已清空"状态）。

_PROVIDER_KEY_VARS = [
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY",
    "SEEDREAM_API_KEY", "VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY",
    "DASHSCOPE_API_KEY", "BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY",
    "ECOMM_API_KEY", "ECOMM_TENANT_KEYS", "ECOMM_WEBHOOK_TOKEN",
]


def _selects_real(markexpr: str) -> bool:
    """判断 -m 表达式是否实际**选中** real 标记。

    不能做子串匹配：addopts 默认表达式是 `not real and not slow`，
    字符串包含 "real" 会误判为选中 real，导致本 fixture 跳过清 Key、
    secrets.yaml 注入的真实 Key 污染全量测试（P5 实测踩坑）。
    用 pytest 自带的标记表达式求值器；异常时回退保守词法扫描。
    """
    expr = (markexpr or "").strip()
    if not expr:
        return False
    try:
        from _pytest.mark.expression import Expression
        return Expression.compile(expr).evaluate(lambda key: key == "real")
    except Exception:
        words = expr.replace("(", " ").replace(")", " ").split()
        for i, w in enumerate(words):
            if w == "real" and (i == 0 or words[i - 1] != "not"):
                return True
        return False


@pytest.fixture(scope="session", autouse=True)
def _isolate_runtime_dirs(tmp_path_factory):
    """第三轮审计 B2-18：把运行期数据目录与配置根重定向到 tmp。

    此前后端测试直写真实目录：`data/{checkpoints,audit,memory}`（实测 checkpoint
    累积 1600+ 文件）与真实 `config/*.yaml`（仅靠 finally 还原，崩溃即半写）。

    做法（product 侧配套能力，见 `src/core/config.py`）：
    - `ECOMM_DATA_DIR` → tmp/data（checkpoint / audit / memory / workflow.db）；
    - `ECOMM_PROJECT_ROOT` → tmp 副本（config/ 全量拷贝，**排除密钥文件**）。

    必须是 session 级且在 `_deterministic_mock_env` 之前生效：后者要重建注册表，
    此时配置读取已经走 tmp 副本。
    """
    real_root = Path(__file__).resolve().parent.parent
    root = tmp_path_factory.mktemp("run_root")
    shutil.copytree(
        real_root / "config", root / "config",
        ignore=shutil.ignore_patterns("secrets.yaml", "tenant_keys.yaml"),  # 密钥不进测试副本
    )
    saved = {k: os.environ.get(k) for k in ("ECOMM_PROJECT_ROOT", "ECOMM_DATA_DIR")}
    os.environ["ECOMM_PROJECT_ROOT"] = str(root)
    os.environ["ECOMM_DATA_DIR"] = str(root / "data")

    yield root

    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _restore_credential_env():
    """逐个测试快照/恢复凭据与目录类环境变量。

    有些生产代码**直接写 os.environ**（保存 API Key、迁移凭据 move-credential、
    设置输出目录、_isolate 之外的目录覆盖），monkeypatch 回滚不了它们——
    实测踩到：迁移凭据的用例把 ARK_API_KEY 留在 env 里，导致后续
    `test_no_keys_only_mock` 看到 ark 可用而失败。
    变量清单是动态的（路由表的凭据变量 + secrets.yaml 注入过的变量）。
    """
    names = set(_PROVIDER_KEY_VARS) | {
        "MOCK_MODE", "ECOMM_OUTPUT_DIR", "ECOMM_PROJECT_ROOT", "ECOMM_DATA_DIR",
    }
    # 注意语义：env_provided_secret_keys() 只记录"真正由部署环境提供"的键
    # （secrets.yaml 注入的**不算**，因为那些在设置页里是可改的）。
    # 测试要清的是"进程里可能存在的任何凭据" → 必须并上 secrets.yaml 的键，
    # 否则实盘 secrets.yaml 里新增的键（如 ARK_API_KEY）会一直留在 env 里。
    try:
        from src.core.config import env_provided_secret_keys, load_runtime_secrets
        names |= set(env_provided_secret_keys()) | set(load_runtime_secrets())
    except Exception:  # noqa: BLE001 — 配置读取失败也不能挡住测试
        pass
    try:
        from src.providers import all_route_specs   # 注意：在包 __init__ 里，不在 routes
        for spec in all_route_specs().values():
            names |= set(spec.key_envs) | set(spec.all_key_envs)
    except Exception:  # noqa: BLE001
        pass

    saved = {k: os.environ.get(k) for k in names}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session", autouse=True)
def _deterministic_mock_env(pytestconfig):
    # P4：`pytest -m real` 时保留 Key 与真实环境（真实套件缺 Key 自行 skip），
    # 不强制 Mock——否则 secrets.yaml 注入的 Key 会被本 fixture 清掉。
    markexpr = pytestconfig.getoption("markexpr", "") or ""
    if _selects_real(markexpr):
        yield
        return

    saved = {k: os.environ.get(k) for k in _PROVIDER_KEY_VARS}
    saved_mock = os.environ.get("MOCK_MODE")

    # 顺序陷阱：src.core.config 导入时会执行 apply_runtime_secrets_to_env()
    # 把 secrets.yaml 的 Key 注入 env——必须先完成全部导入，再清 Key，
    # 最后重建注册表，否则重建出的注册表又带真实 Provider。
    from src.providers import reset_provider_registry  # noqa: F401
    import src.main as main_mod

    # 静态清单不够：secrets.yaml 里可能存着**任意**变量名（自定义服务商的凭据、
    # 手工加的其他 Key），漏掉就会在整轮测试里残留为 env（实测：遗留的
    # ZHIPU_API_KEY 让"保存自定义服务商 Key"的用例被 403 env-locked 拦下；
    # ARK_API_KEY 让 test_no_keys_only_mock 误判 ark 可用）。
    # 这里以"secrets.yaml 的全部键 + 真正由部署环境提供的键"为准动态补全。
    from src.core.config import env_provided_secret_keys, load_runtime_secrets
    leaked = set(env_provided_secret_keys()) | set(load_runtime_secrets()) | set(saved)
    saved = {k: saved.get(k) for k in leaked}
    for k in leaked:
        os.environ.pop(k, None)
    os.environ["MOCK_MODE"] = "true"

    # 重建注册表：清掉 collection 期可能已带真实 Key 构建的 Provider/Agent
    main_mod._provider_registry = reset_provider_registry()
    asyncio.run(main_mod._agent_registry.load_from_config(main_mod._provider_registry))

    yield

    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    if saved_mock is None:
        os.environ.pop("MOCK_MODE", None)
    else:
        os.environ["MOCK_MODE"] = saved_mock


@pytest.fixture
def sample_image_base64() -> str:
    """生成一张 1x1 像素的测试图片 base64"""
    # 最小的 JPEG
    img_bytes = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00\x43\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\x09\x09"
        b"\x08\x0a\x0c\x14\x0d\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f"
        b"\x1e\x1d\x1a\x1c\x1c\x20\x24\x2e\x27\x20\x22\x2c\x23\x1c\x1c\x28\x37"
        b"\x29\x2c\x30\x31\x34\x34\x34\x1f\x27\x39\x3d\x38\x32\x3c\x2e\x33\x34"
        b"\x32\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00"
        b"\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\xff\xc4\x00\xb5"
        b"\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01\x7d"
        b"\x01\x02\x03\x00\x04\x11\x05\x12\x21\x31\x41\x06\x13\x51\x61\x07\x22"
        b"\x71\x14\x32\x81\x91\xa1\x08\x23\x42\xb1\xc1\x15\x52\xd1\xf0\x24\x33"
        b"\x62\x72\x82\x09\x0a\x16\x17\x18\x19\x1a\x25\x26\x27\x28\x29\x2a\x34"
        b"\x35\x36\x37\x38\x39\x3a\x43\x44\x45\x46\x47\x48\x49\x4a\x53\x54\x55"
        b"\x56\x57\x58\x59\x5a\x63\x64\x65\x66\x67\x68\x69\x6a\x73\x74\x75\x76"
        b"\x77\x78\x79\x7a\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96"
        b"\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5"
        b"\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4"
        b"\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1"
        b"\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x0c\x03\x01\x00\x02"
        b"\x11\x03\x11\x00\x3f\x00\xa0\x0b\xff\xd9"
    )
    return base64.b64encode(img_bytes).decode("utf-8")


@pytest.fixture
def session_factory():
    """创建测试用的 SessionState"""
    from src.chat.session import SessionManager
    mgr = SessionManager()
    return mgr


@pytest.fixture
def sample_analysis() -> dict:
    from src.providers.mock import MOCK_ANALYSIS
    return dict(MOCK_ANALYSIS)
