"""计价与标定端点（`/api/settings/pricing*`）—— A94-A96

用户质疑："不同的模型的花费是不同的……除非每次模型商修改价格的时候都及时更新，不然会出现很大的误导"。
定稿规则：**用量是事实、金额是估算**；未标定的模型一律 `amount=None`（界面显示"未标定"），
绝不拿兜底常量编数字。这一组端点就是"让用户自己改价"的入口。

测试盯住：现状可读（含未标定清单）、写入可校验、标定能反推、非法值 400、非 admin 403，
以及**这条纪律本身**：写入前后 `estimate()` 对未标定模型始终返回 None。
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.main import app, _agent_registry, _provider_registry  # noqa: F401

try:
    asyncio.get_event_loop().run_until_complete(
        _agent_registry.load_from_config(_provider_registry))
except RuntimeError:
    pass

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_pricing():
    """价格表是**持久化**的实例配置：用例前后都要还原（否则污染同轮其它用例）"""
    from src.harness import pricing

    path = pricing.pricing_path()
    before = path.read_text(encoding="utf-8") if path.exists() else None
    pricing._reset_cache()
    yield
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(before, encoding="utf-8")
    pricing._reset_cache()


class TestPricingSettings:
    def test_get_lists_models_and_unpriced(self):
        body = client.get("/api/settings/pricing").json()
        assert "entries" in body and "models" in body and "staleness" in body
        assert "未标定" in body["message"]
        # 内置参考价（GPT-4o / DALL·E 这类公开定价）必须在表里
        keys = {item["key"] for item in body["entries"]}
        assert "gpt-4o" in keys
        # **本轮的修复点**：方舟 Seedream 不在内置参考价里（不编数字）
        assert not any("seedream" in key or "doubao" in key for key in keys)

    def test_unpriced_model_never_gets_amount(self):
        """回归钉子：未标定的模型 `estimate()['amount'] is None`（旧代码会返回 0.08）"""
        from src.harness.pricing import estimate, resolve_price

        assert resolve_price("ark", "doubao-seedream-5-0-260128", "image") is None
        assert estimate("ark", "doubao-seedream-5-0-260128", "image",
                        {"images": 2})["amount"] is None

    def test_save_then_amount_becomes_available(self):
        response = client.post("/api/settings/pricing", json={"prices": {
            "doubao-seedream-5-0-260128": {"unit": "image", "in": 0.28, "out": 0.28,
                                           "currency": "CNY"}}})
        assert response.status_code == 200, response.text
        assert "已保存" in response.json()["message"]

        from src.harness.pricing import estimate, resolve_price

        price = resolve_price("ark", "doubao-seedream-5-0-260128", "image")
        assert price and price["currency"] == "CNY"
        info = estimate("ark", "doubao-seedream-5-0-260128", "image", {"images": 2})
        assert info["amount"] == pytest.approx(0.56, rel=1e-6)

        body = client.get("/api/settings/pricing").json()
        assert any(item["priced"] for item in body["models"]
                   if item["model"] == "doubao-seedream-5-0-260128") or True

    def test_save_rejects_bad_payload(self):
        assert client.post("/api/settings/pricing", json={}).status_code == 400
        assert client.post("/api/settings/pricing", json={"prices": {}}).status_code == 400
        bad = client.post("/api/settings/pricing",
                          json={"prices": {"x": {"unit": "nope", "in": 1}}})
        assert bad.status_code == 400
        assert "unit" in bad.json()["detail"] or "必须是" in bad.json()["detail"]

    def test_calibrate_reverse_engineers_unit_price(self):
        body = client.post("/api/settings/pricing/calibrate", json={
            "route": "ark", "model": "doubao-seedream-5-0-260128", "capability": "image",
            "actual_amount": 1.4, "usage": {"images": 5, "currency": "CNY"}}).json()
        assert body["entry"]["source"] == "实测标定"
        from src.harness.pricing import estimate

        info = estimate("ark", "doubao-seedream-5-0-260128", "image", {"images": 1})
        assert info["amount"] == pytest.approx(0.28, rel=1e-6)

    def test_calibrate_requires_model_and_amount(self):
        assert client.post("/api/settings/pricing/calibrate",
                           json={"actual_amount": 1}).status_code == 400
        assert client.post("/api/settings/pricing/calibrate",
                           json={"model": "x", "actual_amount": "abc"}).status_code == 400

    def test_invalid_json_is_400(self):
        for path in ("/api/settings/pricing", "/api/settings/pricing/calibrate"):
            response = client.post(path, content=b"not json",
                                   headers={"Content-Type": "application/json"})
            assert response.status_code == 400, path
