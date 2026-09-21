"""设置/会话列表端点测试 — /api/settings、/api/sessions、Agent 参数持久化"""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from src.main import _agent_registry, _provider_registry, _session_manager, app

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
        for key in ("mock_mode", "api_keys", "tenant_keys", "providers", "agents", "models_config", "model_catalog", "cors_origins"):
            assert key in data, f"缺少字段 {key}"

    def test_settings_agents_have_resolved_model(self):
        """每个 Agent 都带当前生效模型（模型映射解析结果）"""
        data = client.get("/api/settings").json()
        for a in data["agents"]:
            assert "resolved_model" in a, f"{a['name']} 缺少 resolved_model"
            assert a["resolved_model"]  # Mock 模式下为 mock

    def test_settings_api_keys_masked(self):
        """审计修复：不返回任何密钥片段（configured 布尔即可）"""
        data = client.get("/api/settings").json()
        assert len(data["api_keys"]) >= 8
        for k in data["api_keys"]:
            assert "env" in k
            assert "configured" in k
            assert "masked" not in k
            assert isinstance(k["configured"], bool)

    def test_settings_agents_include_params(self):
        data = client.get("/api/settings").json()
        names = [a["name"] for a in data["agents"]]
        assert "商品分析员" in names
        analyst = next(a for a in data["agents"] if a["name"] == "商品分析员")
        assert analyst["config_file"] == "analyst"
        assert any(p["key"] == "detail_level" for p in analyst["params"])


class TestProviderConfig:
    """P8：Provider 端点与模型配置（第三方 coding plan / 代理 / 自建网关）"""

    def _isolate(self, tmp_path, monkeypatch):
        """端点配置文件与端点环境变量都隔离到 tmp"""
        import src.core.config as cfg
        monkeypatch.setattr(cfg, "PROVIDER_CONFIG_REL", str(tmp_path / "providers.yaml"))
        for name in ("OPENAI_BASE_URL", "ECOMM_OPENAI_BASE_URL",
                     "ANTHROPIC_BASE_URL", "DEEPSEEK_BASE_URL",
                     "QWEN_BASE_URL", "DASHSCOPE_BASE_URL"):
            monkeypatch.delenv(name, raising=False)

    def test_route_metadata_in_settings(self):
        routes = {r["route"]: r for r in client.get("/api/settings").json()["provider_routes"]}
        assert set(routes) >= {"openai", "deepseek", "anthropic", "qwen", "seedream", "flux"}
        assert routes["openai"]["default_base_url"] == "https://api.openai.com/v1"
        assert routes["openai"]["base_url_supported"] is True
        # 图像路由多上游 → 端点固定
        assert routes["seedream"]["base_url_supported"] is False
        assert routes["flux"]["base_url_supported"] is False
        assert routes["openai"]["official_models"]

    def test_save_base_url_and_models(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        resp = client.post("/api/settings/providers/openai", json={
            "base_url": "https://proxy.example.com/v1/",           # 尾斜杠会被规范化
            "models": ["gpt-5-codex", "claude-sonnet-4-5", "   ", "gpt-5-codex"],
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        route = next(r for r in data["provider_routes"] if r["route"] == "openai")
        assert route["effective_base_url"] == "https://proxy.example.com/v1"
        assert route["base_url_custom"] is True
        assert route["base_url_source"] == "file"
        assert route["models"] == ["gpt-5-codex", "claude-sonnet-4-5"]   # 去空行 + 去重
        # 自定义模型并入模型映射建议目录
        assert "gpt-5-codex" in data["model_catalog"]["openai"]
        # 落盘（重启后仍生效）
        from src.core.config import load_provider_config
        assert load_provider_config()["openai"]["base_url"] == "https://proxy.example.com/v1"

    def test_base_url_validation(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        for bad in ("ftp://x.example", "not-a-url", "http://", "https://"):
            resp = client.post("/api/settings/providers/openai", json={"base_url": bad})
            assert resp.status_code == 400, f"{bad} 应被拒绝: {resp.text}"
        assert client.post("/api/settings/providers/openai",
                           json={"models": "nope"}).status_code == 400
        assert client.post("/api/settings/providers/openai", json={}).status_code == 400
        # 非法模型 id（中文/空格）
        assert client.post("/api/settings/providers/openai",
                           json={"models": ["中文模型"]}).status_code == 400
        assert client.post("/api/settings/providers/openai",
                           json={"models": ["a b"]}).status_code == 400

    def test_unknown_route_404(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        assert client.post("/api/settings/providers/nope",
                           json={"models": ["x"]}).status_code == 404

    def test_image_route_models_ok_base_url_rejected(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        assert client.post("/api/settings/providers/seedream",
                           json={"base_url": "https://x.example/v1"}).status_code == 400
        resp = client.post("/api/settings/providers/seedream", json={"models": ["seedream-6.0"]})
        assert resp.status_code == 200, resp.text
        route = next(r for r in resp.json()["provider_routes"] if r["route"] == "seedream")
        assert route["models"] == ["seedream-6.0"]

    def test_clear_restores_official_endpoint(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        client.post("/api/settings/providers/openai",
                    json={"base_url": "https://p.example.com/v1"})
        resp = client.post("/api/settings/providers/openai", json={"base_url": ""})
        assert resp.status_code == 200, resp.text
        route = next(r for r in resp.json()["provider_routes"] if r["route"] == "openai")
        assert route["effective_base_url"] == "https://api.openai.com/v1"
        assert route["base_url_custom"] is False
        assert route["base_url_source"] == ""

    def test_env_base_url_wins_and_locks(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.com/v1")
        route = next(r for r in client.get("/api/settings").json()["provider_routes"]
                     if r["route"] == "openai")
        assert route["effective_base_url"] == "https://env.example.com/v1"
        assert route["base_url_source"] == "env"
        # env 供给时设置页写入被拒（不会静默失效）
        resp = client.post("/api/settings/providers/openai",
                           json={"base_url": "https://other.example.com/v1"})
        assert resp.status_code == 403
        assert "环境变量" in resp.json()["detail"]

    def test_provider_honors_override(self, tmp_path, monkeypatch):
        """真正落到 Provider 实例：端点覆盖生效且 env 优先"""
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        client.post("/api/settings/providers/openai",
                    json={"base_url": "https://proxy.example.com/v1"})
        from src.providers.openai import OpenAIImageProvider, OpenAILLMProvider
        assert OpenAILLMProvider().base_url == "https://proxy.example.com/v1"
        assert OpenAIImageProvider().base_url == "https://proxy.example.com/v1"
        # env 优先级最高
        monkeypatch.setenv("OPENAI_BASE_URL", "https://env.example.com/v1")
        assert OpenAILLMProvider().base_url == "https://env.example.com/v1"
        # 清除 env 后回落文件配置
        monkeypatch.delenv("OPENAI_BASE_URL")
        assert OpenAILLMProvider().base_url == "https://proxy.example.com/v1"

    def test_qwen_env_alias(self, tmp_path, monkeypatch):
        """qwen 兼容 DashScope 官方命名 DASHSCOPE_BASE_URL"""
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope-proxy.example.com/v1")
        from src.providers.qwen import QwenLLMProvider
        assert QwenLLMProvider().base_url == "https://dashscope-proxy.example.com/v1"


class TestApiKeyUpdate:
    """POST /api/settings/api-keys — 运行时密钥持久化"""

    def test_unknown_key_rejected(self):
        resp = client.post("/api/settings/api-keys", json={"api_keys": {"NOT_A_KEY": "x"}})
        assert resp.status_code == 400

    def test_bad_body_rejected(self):
        resp = client.post("/api/settings/api-keys", json={"api_keys": "oops"})
        assert resp.status_code == 400

    def test_env_provided_key_rejected_403(self, monkeypatch):
        """P7：环境变量供给的密钥 → 设置页保存显式拒绝（防"改了不生效"陷阱）"""
        import src.core.config as cfg
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
        monkeypatch.setattr(cfg, "_ENV_PROVIDED_SECRETS", {"DEEPSEEK_API_KEY"})
        resp = client.post("/api/settings/api-keys",
                           json={"api_keys": {"DEEPSEEK_API_KEY": "sk-new"}})
        assert resp.status_code == 403
        assert "环境变量" in resp.json()["detail"]
        assert "DEEPSEEK_API_KEY" in resp.json()["detail"]

    def test_unknown_key_still_400_before_env_check(self, monkeypatch):
        """校验顺序：未知密钥仍是 400（不被 env 锁定检查抢先变 403）"""
        import src.core.config as cfg
        monkeypatch.setattr(cfg, "_ENV_PROVIDED_SECRETS", {"DEEPSEEK_API_KEY"})
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
        resp = client.post("/api/settings/api-keys",
                           json={"api_keys": {"NOT_A_KEY": "x"}})
        assert resp.status_code == 400


class TestProviderKeyMeta:
    """P7：provider 行的状态所需元信息（provider 路由 / 可用性 / 密钥来源）"""

    def test_api_keys_carry_provider_and_available(self):
        data = client.get("/api/settings").json()
        # 路由集合从载荷推导（此前写死 6 条，新增 ark 后失效）
        routes = {r["route"] for r in data["provider_routes"]}
        assert len(data["api_keys"]) >= 10
        for k in data["api_keys"]:
            assert k["provider"] in routes, f"{k['env']} 缺少 provider 路由"
            assert isinstance(k["available"], bool)
            assert "source" in k
        # 火山引擎方舟：即梦/Seedream 真正的服务商（用户订正：即梦不是服务商）
        assert "ark" in routes
        assert any(k["env"] == "ARK_API_KEY" for k in data["api_keys"])

    def test_source_and_availability_flow(self):
        """未配置 → source '' / available False；保存后 → source 'file' / available True"""
        import os

        from src.core.config import load_runtime_secrets, save_runtime_secrets

        orig_secrets = load_runtime_secrets()
        orig_env = os.environ.get("DEEPSEEK_API_KEY")
        os.environ.pop("DEEPSEEK_API_KEY", None)
        try:
            # 干净起点：未配置 → 无来源、注册表无该路由
            entry = next(k for k in client.get("/api/settings").json()["api_keys"]
                         if k["env"] == "DEEPSEEK_API_KEY")
            assert entry["configured"] is False
            assert entry["source"] == ""
            assert entry["available"] is False

            # 经设置页保存 → 文件来源 + 注册表立即可用（rebuild）
            resp = client.post("/api/settings/api-keys",
                               json={"api_keys": {"DEEPSEEK_API_KEY": "sk-file-managed"}})
            assert resp.status_code == 200, resp.text
            entry = next(k for k in resp.json()["api_keys"] if k["env"] == "DEEPSEEK_API_KEY")
            assert entry["configured"] is True
            assert entry["source"] == "file"
            assert entry["available"] is True
        finally:
            save_runtime_secrets({"DEEPSEEK_API_KEY": orig_secrets.get("DEEPSEEK_API_KEY", "")})
            if orig_env is None:
                os.environ.pop("DEEPSEEK_API_KEY", None)
            else:
                os.environ["DEEPSEEK_API_KEY"] = orig_env
            import src.main as main_mod
            from src.providers import reset_provider_registry
            main_mod._provider_registry = reset_provider_registry()
            _run_async(_agent_registry.load_from_config(main_mod._provider_registry))

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
            assert resp.status_code == 200, resp.text
            data = resp.json()
            openai = next(k for k in data["api_keys"] if k["env"] == "OPENAI_API_KEY")
            assert openai["configured"] is True
            # 审计修复：不再返回任何密钥片段
            assert "masked" not in openai
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
            import src.main as main_mod
            from src.providers import reset_provider_registry
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
            import src.main as main_mod
            from src.providers import reset_provider_registry
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


class TestAgentOverrideIssues:
    """A41：覆盖键与该 Agent 的 requires 不匹配时必须能被发现

    实测：models.yaml 给「审查员」写 `text: deepseek/...`，但它 requires=vision →
    覆盖被静默忽略，用户以为改了模型其实没改（跑的还是 vision 默认模型）。
    """

    def test_mismatched_override_is_reported(self, monkeypatch):
        import src.api.main as main_mod
        monkeypatch.setattr(main_mod, "load_models_config", lambda: {
            "capabilities": {},
            "agent_overrides": {"审查员": {"text": "deepseek/deepseek-v4-flash"}},
        })

        data = client.get("/api/settings").json()

        issues = data.get("agent_overrides_issues") or []
        assert any(i["agent"] == "审查员" and i["capability"] == "text" for i in issues), issues
        assert issues[0]["requires"] == ["vision"]
        assert "不会生效" in issues[0]["reason"]

    def test_shipped_config_has_no_issues(self):
        """仓库自带的 models.yaml 不应该有失效覆盖"""
        data = client.get("/api/settings").json()
        assert data.get("agent_overrides_issues") == []

    def test_unknown_agent_override_is_reported(self, monkeypatch):
        import src.api.main as main_mod
        monkeypatch.setattr(main_mod, "load_models_config", lambda: {
            "capabilities": {},
            "agent_overrides": {"不存在的 Agent": {"text": "deepseek/x"}},
        })
        issues = client.get("/api/settings").json()["agent_overrides_issues"]
        assert any("不存在" in i["reason"] for i in issues)


class TestChatSettings:
    """B1：会话策略（审查/合规连续失败 N 次即停）可在设置页调整"""

    def test_settings_payload_exposes_chat_policy(self):
        data = client.get("/api/settings").json()
        chat = data.get("chat") or {}
        assert "max_consecutive_review_failures" in chat
        assert chat["max_consecutive_review_failures"] >= 0

    def test_update_policy_persists_and_takes_effect(self, monkeypatch):
        import src.api.main as main_mod

        saved = {}
        monkeypatch.setattr(main_mod, "save_chat_settings",
                            lambda updates: saved.update(updates) or {
                                "max_consecutive_review_failures": updates.get(
                                    "max_consecutive_review_failures", 2),
                                "max_turns": 15, "session_ttl_hours": 24.0})

        resp = client.post("/api/settings/chat",
                           json={"max_consecutive_review_failures": 5})

        assert resp.status_code == 200
        assert saved["max_consecutive_review_failures"] == 5

    def test_unknown_field_400(self):
        resp = client.post("/api/settings/chat", json={"nope": 1})
        assert resp.status_code == 400
        assert "不支持的字段" in resp.json()["detail"]

    def test_empty_body_400(self):
        assert client.post("/api/settings/chat", json={}).status_code == 400

    def test_invalid_json_400(self):
        resp = client.post("/api/settings/chat", content=b"not json",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_normalize_rejects_bad_values(self):
        from src.core.config import (
            DEFAULT_MAX_CONSECUTIVE_FAILURES,
            normalize_max_consecutive_failures,
        )

        assert normalize_max_consecutive_failures(3) == 3
        assert normalize_max_consecutive_failures("4") == 4
        assert normalize_max_consecutive_failures(0) == 0        # 关闭
        assert normalize_max_consecutive_failures(-1) == DEFAULT_MAX_CONSECUTIVE_FAILURES
        assert normalize_max_consecutive_failures("abc") == DEFAULT_MAX_CONSECUTIVE_FAILURES
        assert normalize_max_consecutive_failures(999) == 20      # 上限保护


class TestChatSettingsStorage:
    """会话策略写进 config/chat.yaml（独立文件）——不能改写 default.yaml 丢注释"""

    def test_save_writes_dedicated_file(self, tmp_path):
        from src.core.config import chat_settings, save_chat_settings

        saved = save_chat_settings({"max_consecutive_review_failures": 3})

        assert saved["max_consecutive_review_failures"] == 3
        import src.core.config as cfg_mod
        chat_file = cfg_mod._project_root() / "config" / "chat.yaml"
        assert chat_file.exists(), "应写入 config/chat.yaml"
        assert "max_consecutive_review_failures: 3" in chat_file.read_text(encoding="utf-8")
        assert chat_settings()["max_consecutive_review_failures"] == 3

    def test_default_yaml_is_not_rewritten(self):
        """default.yaml 里的注释是文档的一部分，程序不得改写它"""
        import hashlib

        import src.core.config as cfg_mod
        from src.core.config import save_chat_settings

        path = cfg_mod._project_root() / "config" / "default.yaml"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        save_chat_settings({"max_consecutive_review_failures": 1, "max_turns": 12})
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    def test_invalid_values_rejected(self):
        from src.core.config import save_chat_settings

        with pytest.raises(ValueError):
            save_chat_settings({"max_consecutive_review_failures": 99})
        with pytest.raises(ValueError):
            save_chat_settings({"max_consecutive_review_failures": "abc"})
        with pytest.raises(ValueError):
            save_chat_settings({"max_turns": 0})
        with pytest.raises(ValueError):
            save_chat_settings({"nope": 1})

    def test_override_precedence(self):
        """chat.yaml（设置页）覆盖 default.yaml，其余字段回落默认"""
        from src.core.config import chat_settings, save_chat_settings

        save_chat_settings({"max_turns": 9})
        settings = chat_settings()
        assert settings["max_turns"] == 9
        assert settings["session_ttl_hours"] == 24.0, "未覆盖的字段取 default.yaml"


class TestStyleLibrarySettings:
    """风格档案库开关与条数上限（A79-A96，用户指定的「风格词库」）"""

    def test_defaults_are_on(self):
        """默认 = **一套图只用一套风格词**（用户 2026-09-20："一轮会话里只用一套风格词"）

        此前默认 2 —— 实测每张图被「你的风格」与一套内置原型同时指挥，而两者常互相矛盾。
        """
        from src.core.config import chat_settings

        settings = chat_settings()
        assert settings["style_library_enabled"] is True
        assert settings["style_library_max"] == 1

    def test_save_and_read_back(self):
        from src.core.config import chat_settings, save_chat_settings

        save_chat_settings({"style_library_enabled": False, "style_library_max": 3})
        settings = chat_settings()
        assert settings["style_library_enabled"] is False
        assert settings["style_library_max"] == 3

    def test_default_yaml_not_rewritten(self):
        import hashlib

        import src.core.config as cfg_mod
        from src.core.config import save_chat_settings

        path = cfg_mod._project_root() / "config" / "default.yaml"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        save_chat_settings({"style_library_max": 1})
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    def test_invalid_values_rejected(self):
        from src.core.config import STYLE_LIBRARY_MAX_CEILING, save_chat_settings

        with pytest.raises(ValueError, match="style_library_max"):
            save_chat_settings({"style_library_max": STYLE_LIBRARY_MAX_CEILING + 1})
        with pytest.raises(ValueError, match="style_library_max"):
            save_chat_settings({"style_library_max": "abc"})
        with pytest.raises(ValueError, match="style_library_enabled"):
            save_chat_settings({"style_library_enabled": "yes"})

    def test_zero_means_no_injection(self):
        """0 = 不注入（关闭的另一种表达），策略解析要认这个值"""
        from src.core.config import save_chat_settings
        from src.harness.style_library import resolve_policy

        save_chat_settings({"style_library_max": 0})
        assert resolve_policy({})["max_entries"] == 0

    def test_policy_honours_settings(self):
        """设置页的开关/条数要真的作用到检索（不是只存起来好看的）"""
        from src.core.config import save_chat_settings
        from src.harness.style_library import resolve_policy, select_by_slot

        save_chat_settings({"style_library_enabled": False})
        assert resolve_policy({})["enabled"] is False
        result = select_by_slot([{"slot_id": "main_white", "kind": "photo"}],
                                platform="taobao")
        assert result["enabled"] is False and result["entries"] == []

        save_chat_settings({"style_library_enabled": True, "style_library_max": 1})
        result = select_by_slot([{"slot_id": "main_scene", "kind": "photo"}],
                                analysis={"category": "保健品"}, platform="taobao")
        assert len(result["slots"]["main_scene"]) == 1
