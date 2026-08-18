"""设置/会话列表端点测试 — /api/settings、/api/sessions、Agent 参数持久化"""

import asyncio
import os
import pytest
from fastapi.testclient import TestClient

from src.main import app, _agent_registry, _provider_registry, _session_manager

try:
    asyncio.get_event_loop().run_until_complete(
        _agent_registry.load_from_config(_provider_registry)
    )
except RuntimeError:
    pass

client = TestClient(app)


def _run_async(coro):
    """在全新事件循环中执行协程（pytest-asyncio 可能已关闭主线程循环）"""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestSessionList:
    """GET /api/sessions — 会话列表"""

    def test_list_sessions_shape(self):
        resp = client.get("/api/sessions", headers={"X-Tenant-ID": "default"})
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert "total" in data
        assert data["total"] == len(data["sessions"])

    def test_list_sessions_contains_created(self):
        s = _session_manager.create(
            product_images=["fake_b64"],
            product_info="测试商品",
            platform="taobao",
            tenant_id="default",
        )
        resp = client.get("/api/sessions", headers={"X-Tenant-ID": "default"})
        ids = [item["session_id"] for item in resp.json()["sessions"]]
        assert s["session_id"] in ids
        item = next(i for i in resp.json()["sessions"] if i["session_id"] == s["session_id"])
        assert item["status"] == "created"
        assert item["platform"] == "taobao"
        assert item["product_info"] == "测试商品"

    def test_list_sessions_tenant_isolation(self):
        """其他租户看不到 default 租户的会话"""
        resp = client.get("/api/sessions", headers={"X-Tenant-ID": "tenant_x"})
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestSettingsEndpoint:
    """GET /api/settings — 设置汇总"""

    def test_settings_shape(self):
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("mock_mode", "api_keys", "providers", "agents", "models_config", "model_catalog", "cors_origins"):
            assert key in data, f"缺少字段 {key}"

    def test_settings_agents_have_resolved_model(self):
        """每个 Agent 都带当前生效模型（模型映射解析结果）"""
        data = client.get("/api/settings").json()
        for a in data["agents"]:
            assert "resolved_model" in a, f"{a['name']} 缺少 resolved_model"
            assert a["resolved_model"]  # Mock 模式下为 mock

    def test_settings_api_keys_masked(self):
        data = client.get("/api/settings").json()
        assert len(data["api_keys"]) >= 8
        for k in data["api_keys"]:
            assert "env" in k
            assert "configured" in k
            assert "masked" in k
            # 未配置时 masked 为空；配置时必须是脱敏形式（不含原始值长度）
            if k["configured"]:
                assert len(k["masked"]) <= 8 or "***" in k["masked"]

    def test_settings_agents_include_params(self):
        data = client.get("/api/settings").json()
        names = [a["name"] for a in data["agents"]]
        assert "商品分析员" in names
        analyst = next(a for a in data["agents"] if a["name"] == "商品分析员")
        assert analyst["config_file"] == "analyst"
        assert any(p["key"] == "detail_level" for p in analyst["params"])


class TestApiKeyUpdate:
    """POST /api/settings/api-keys — 运行时密钥持久化"""

    def test_unknown_key_rejected(self):
        resp = client.post("/api/settings/api-keys", json={"api_keys": {"NOT_A_KEY": "x"}})
        assert resp.status_code == 400

    def test_bad_body_rejected(self):
        resp = client.post("/api/settings/api-keys", json={"api_keys": "oops"})
        assert resp.status_code == 400

    def test_set_and_clear_persists(self):
        """设置 → 立即生效 + 落盘；清空 → 恢复

        审计修复：finally 按快照精确还原（原值存在则写回、不存在则删除），
        此前无条件 pop/删除会不可逆抹除开发者真实配置的 OPENAI_API_KEY。
        """
        from src.core.config import load_runtime_secrets, save_runtime_secrets
        orig_env = os.environ.get("OPENAI_API_KEY")
        orig_secrets = load_runtime_secrets()
        try:
            # 设置
            resp = client.post(
                "/api/settings/api-keys",
                json={"api_keys": {"OPENAI_API_KEY": "sk-test-12345"}},
            )
            assert resp.status_code == 200
            data = resp.json()
            openai = next(k for k in data["api_keys"] if k["env"] == "OPENAI_API_KEY")
            assert openai["configured"] is True
            assert openai["masked"] == "sk-t***2345"
            assert os.environ.get("OPENAI_API_KEY") == "sk-test-12345"
            # 已落盘
            assert load_runtime_secrets().get("OPENAI_API_KEY") == "sk-test-12345"

            # 清空
            resp = client.post(
                "/api/settings/api-keys",
                json={"api_keys": {"OPENAI_API_KEY": ""}},
            )
            assert resp.status_code == 200
            data = resp.json()
            openai = next(k for k in data["api_keys"] if k["env"] == "OPENAI_API_KEY")
            assert openai["configured"] is False
            assert os.environ.get("OPENAI_API_KEY") is None
            assert "OPENAI_API_KEY" not in load_runtime_secrets()
        finally:
            # 快照还原：原值存在则写回，否则确保删除
            if orig_env is not None:
                os.environ["OPENAI_API_KEY"] = orig_env
            else:
                os.environ.pop("OPENAI_API_KEY", None)
            if "OPENAI_API_KEY" in orig_secrets:
                save_runtime_secrets({"OPENAI_API_KEY": orig_secrets["OPENAI_API_KEY"]})
            else:
                save_runtime_secrets({"OPENAI_API_KEY": ""})
            from src.providers import reset_provider_registry
            import src.main as main_mod
            main_mod._provider_registry = reset_provider_registry()
            _run_async(_agent_registry.load_from_config(main_mod._provider_registry))


class TestAgentParamUpdate:
    """POST /api/settings/agents/{name} — Agent 参数配置持久化"""

    def _original_yaml(self):
        from src.core.config import _project_root
        path = _project_root() / "config" / "agents" / "analyst.yaml"
        return path.read_text(encoding="utf-8")

    def test_unknown_agent_404(self):
        resp = client.post(
            "/api/settings/agents/不存在的Agent",
            json={"defaults": {"detail_level": "detailed"}},
        )
        assert resp.status_code == 404

    def test_unknown_param_400(self):
        resp = client.post(
            "/api/settings/agents/商品分析员",
            json={"defaults": {"not_a_param": 1}},
        )
        assert resp.status_code == 400

    def test_bad_body_400(self):
        resp = client.post("/api/settings/agents/商品分析员", json={"defaults": "oops"})
        assert resp.status_code == 400

    def test_update_param_persists_and_restores(self):
        original = self._original_yaml()
        try:
            resp = client.post(
                "/api/settings/agents/商品分析员",
                json={"defaults": {"detail_level": "detailed", "focus_areas": ["品类识别"]}},
            )
            assert resp.status_code == 200, resp.text
            params = {p["key"]: p["default"] for p in resp.json()["params"]}
            assert params["detail_level"] == "detailed"
            assert params["focus_areas"] == ["品类识别"]

            # 磁盘 YAML 已更新
            assert "detailed" in self._original_yaml()

            # 设置页 GET 也反映新值
            data = client.get("/api/settings").json()
            analyst = next(a for a in data["agents"] if a["name"] == "商品分析员")
            params = {p["key"]: p["default"] for p in analyst["params"]}
            assert params["detail_level"] == "detailed"
        finally:
            # 恢复原始配置文件
            from src.core.config import _project_root
            path = _project_root() / "config" / "agents" / "analyst.yaml"
            path.write_text(original, encoding="utf-8")
            _run_async(_agent_registry.load_from_config(_provider_registry))


class TestModelMappingUpdate:
    """POST /api/settings/models — 能力→模型映射持久化"""

    def _original_yaml(self):
        from src.core.config import _project_root
        return (_project_root() / "config" / "models.yaml").read_text(encoding="utf-8")

    def test_bad_body_400(self):
        assert client.post("/api/settings/models", json={"capabilities": "x"}).status_code == 400
        assert client.post("/api/settings/models", json={"capabilities": {"text": "x"}}).status_code == 400

    def test_update_models_persists_and_restores(self):
        original = self._original_yaml()
        try:
            resp = client.post("/api/settings/models", json={
                "capabilities": {
                    "text": {"default": "qwen/qwen-max",
                             "alternatives": ["deepseek/deepseek-chat"],
                             "fallback": ["mock"]},
                },
                "agent_overrides": {"提示词生成员": {"text": "qwen/qwen-max"}},
            })
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["models_config"]["capabilities"]["text"]["default"] == "qwen/qwen-max"
            assert data["models_config"]["capabilities"]["text"]["alternatives"] == ["deepseek/deepseek-chat"]
            assert data["models_config"]["agent_overrides"]["提示词生成员"] == {"text": "qwen/qwen-max"}

            # 磁盘已持久化
            assert "qwen/qwen-max" in self._original_yaml()
        finally:
            from src.core.config import _project_root
            path = _project_root() / "config" / "models.yaml"
            path.write_text(original, encoding="utf-8")
            from src.providers import reset_provider_registry
            import src.main as main_mod
            main_mod._provider_registry = reset_provider_registry()
            _run_async(_agent_registry.load_from_config(main_mod._provider_registry))


class TestRegistryModelThreading:
    """模型名从 config/models.yaml 解析后必须真正下发到 Agent 实例"""

    def test_create_agent_threads_model(self):
        from src.agents.registry import AgentRegistry
        from src.core.models import AgentMeta
        from src.providers.mock import MockLLMProvider

        reg = AgentRegistry()
        meta = AgentMeta(name="提示词生成员", description="生成提示词", requires=["text"])
        agent = reg._create_agent(meta, MockLLMProvider(), "deepseek-chat")
        assert agent is not None
        assert agent.model_name == "deepseek-chat"
        # Agent 调用 Provider 时会显式传递该模型
        assert agent._model_kwargs() == {"model": "deepseek-chat"}

    def test_no_model_keeps_provider_default(self):
        from src.agents.registry import AgentRegistry
        from src.core.models import AgentMeta
        from src.providers.mock import MockLLMProvider

        reg = AgentRegistry()
        meta = AgentMeta(name="提示词生成员", description="生成提示词", requires=["text"])
        agent = reg._create_agent(meta, MockLLMProvider(), "")
        assert agent is not None
        assert agent.model_name == ""
        assert agent._model_kwargs() == {}  # 用 Provider 默认模型
