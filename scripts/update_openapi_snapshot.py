"""重新生成 OpenAPI 契约快照（tests/fixtures/openapi_snapshot.json）

端点/方法/模式变更后运行：
    python scripts/update_openapi_snapshot.py

对应测试：tests/test_api/test_openapi_snapshot.py（漂移即失败，
迫使契约变更显式化 —— test-plan P3 L3）。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.main import app  # noqa: E402


def build_snapshot() -> dict:
    spec = app.openapi()
    paths = {
        path: sorted(methods) for path, methods in sorted(spec["paths"].items())
    }
    schemas = sorted(spec.get("components", {}).get("schemas", {}).keys())
    return {
        "openapi": spec["openapi"],
        "info": {"title": spec["info"]["title"], "version": spec["info"]["version"]},
        "paths": paths,
        "schemas": schemas,
    }


if __name__ == "__main__":
    out = Path(__file__).parent.parent / "tests" / "fixtures" / "openapi_snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(build_snapshot(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"snapshot written: {out}")
