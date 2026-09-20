"""OpenAPI 契约漂移检测（test-plan P3 L3）

快照 tests/fixtures/openapi_snapshot.json 由
`python scripts/update_openapi_snapshot.py` 生成。
任何端点/方法/Schema 变更必须同步更新快照 —— 本测试失败即
"契约变更未显式化"（作为 API 变更的显式审批点，利大于弊）。
"""

import json
from pathlib import Path

from src.main import app

SNAPSHOT_PATH = Path(__file__).parent.parent / "fixtures" / "openapi_snapshot.json"


def _current() -> dict:
    spec = app.openapi()
    return {
        "paths": {p: sorted(m) for p, m in spec["paths"].items()},
        "schemas": sorted(spec.get("components", {}).get("schemas", {}).keys()),
    }


class TestOpenApiSnapshot:
    def test_snapshot_in_sync(self):
        stored = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        cur = _current()
        assert cur == {"paths": stored["paths"], "schemas": stored["schemas"]}, (
            "OpenAPI 契约漂移！运行 `python scripts/update_openapi_snapshot.py` 更新快照"
        )

    def test_key_endpoints_present(self):
        cur = _current()
        for path in [
            "/api/sessions",
            "/api/sessions/{session_id}/interject",
            "/api/sessions/{session_id}/ab-test",
            "/api/settings/models",
            "/api/settings/tenant-keys",
            "/api/workflows/templates",
            "/api/workflows/templates/{name}/instantiate",
            "/api/workflows/templates/import",
            "/api/workflows/jobs/{job_id}",
            "/api/workflows/jobs/{job_id}/control",
            "/api/workflows/jobs/{job_id}/decision",
            "/api/workflows/jobs/{job_id}/replicate",
            "/api/workflows/batches",
            "/api/workflows/batches/report",
            "/api/workflows/batches/{batch_id}/control",
            "/api/webhooks/workflows/{job_id}/decision",
        ]:
            assert path in cur["paths"], f"关键端点缺失: {path}"

    def test_key_methods_present(self):
        cur = _current()
        # FastAPI openapi() 的 path item 键为小写方法名
        assert "post" in cur["paths"]["/api/workflows/jobs/{job_id}/control"]
        assert "get" in cur["paths"]["/api/workflows/batches/report"]
        assert "delete" in cur["paths"]["/api/sessions/{session_id}"]

    def test_error_schemas_declared(self):
        cur = _current()
        assert "HTTPValidationError" in cur["schemas"]
        assert "ValidationError" in cur["schemas"]
