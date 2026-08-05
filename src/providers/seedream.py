"""Seedream 5.0 (即梦AI) Provider — 字节跳动/火山引擎图像生成"""

import os
import time
import hmac
import hashlib
import json
from datetime import datetime, timezone
from src.providers.base import BaseImageProvider


class SeedreamImageProvider(BaseImageProvider):
    """即梦AI Seedream 5.0 — 中文电商语义最强的图像生成

    支持两种认证方式：
    1. 火山引擎 AK/SK（推荐）
    2. 即梦AI 平台 API Key
    """

    name = "seedream"
    capabilities = ["image"]

    def __init__(self):
        self.ak = os.getenv("VOLCANO_ACCESS_KEY", "")
        self.sk = os.getenv("VOLCANO_SECRET_KEY", "")
        self.api_key = os.getenv("SEEDREAM_API_KEY", "")
        self._use_volc = bool(self.ak and self.sk)

    async def generate(
        self, prompt: str, negative_prompt: str = "", size: str = "1024x1024", model: str = "seedream-5.0"
    ) -> dict:
        import httpx

        if self._use_volc:
            return await self._generate_via_volc(prompt, negative_prompt, size)
        elif self.api_key:
            return await self._generate_via_jimeng(prompt, negative_prompt, size)
        else:
            return {"error": "Seedream 需要 VOLCANO_ACCESS_KEY + VOLCANO_SECRET_KEY 或 SEEDREAM_API_KEY"}

    async def _generate_via_volc(self, prompt: str, negative_prompt: str, size: str) -> dict:
        """通过火山引擎视觉智能 API 生成"""
        import httpx

        # 火山引擎 CV 服务
        service = "cv"
        host = "visual.volcengineapi.com"
        region = "cn-north-1"
        action = "CVProcess"
        version = "2022-08-31"

        w, h = self._parse_size(size)
        body = {
            "req_key": "jimeng_t2i_v51",
            "prompt": prompt,
            "negative_prompt": negative_prompt or "blurry, low quality, distorted, watermark, text",
            "width": w,
            "height": h,
            "return_url": True,
        }

        headers = self._sign_volc_request(service, region, action, version, body)

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"https://{host}/?Action={action}&Version={version}",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"Seedream API error: {resp.status_code}"}

            data = resp.json()
            result = data.get("data", {})
            image_urls = result.get("image_urls", [])

            return {
                "image_url": image_urls[0] if image_urls else "",
            "base64_data": "",
            "model_used": "seedream-5.0",
            "cost_usd": self._estimate_cost(size),
        }

    async def _generate_via_jimeng(self, prompt: str, negative_prompt: str, size: str) -> dict:
        import httpx

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        body = {"prompt": prompt, "negative_prompt": negative_prompt, "size": size}

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.jimeng.ai/v1/images/generations",
                headers=headers, json=body,
            )
            if resp.status_code != 200:
                return {"error": f"即梦AI API error: {resp.status_code}"}

            data = resp.json()
            results = data.get("data", [])
            first = results[0] if results else {}
            return {
                "image_url": first.get("url", ""),
                "base64_data": "",
                "model_used": "seedream-5.0",
                "cost_usd": self._estimate_cost(size),
            }

    def _sign_volc_request(self, service: str, region: str, action: str, version: str, body: dict) -> dict:
        """火山引擎签名 V4"""
        now = datetime.now(tz=timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        body_str = json.dumps(body)

        # Step 1: Canonical Request
        canonical_uri = "/"
        canonical_querystring = f"Action={action}&Version={version}"
        canonical_headers = (
            f"content-type:application/json\n"
            f"host:visual.volcengineapi.com\n"
            f"x-content-sha256:{hashlib.sha256(body_str.encode()).hexdigest()}\n"
            f"x-date:{timestamp}\n"
        )
        signed_headers = "content-type;host;x-content-sha256;x-date"

        canonical_request = (
            f"POST\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n"
            f"{hashlib.sha256(body_str.encode()).hexdigest()}"
        )

        # Step 2: String to Sign
        credential_scope = f"{datestamp}/{region}/{service}/request"
        string_to_sign = (
            f"HMAC-SHA256\n{timestamp}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        # Step 3: Signing Key
        k_date = self._hmac_sha256(self.sk.encode(), datestamp)
        k_region = self._hmac_sha256(k_date, region)
        k_service = self._hmac_sha256(k_region, service)
        signing_key = self._hmac_sha256(k_service, "request")

        # Step 4: Signature
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        authorization = (
            f"HMAC-SHA256 Credential={self.ak}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        return {
            "Content-Type": "application/json",
            "Host": "visual.volcengineapi.com",
            "X-Date": timestamp,
            "X-Content-Sha256": hashlib.sha256(body_str.encode()).hexdigest(),
            "Authorization": authorization,
        }

    def _hmac_sha256(self, key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    def _estimate_cost(self, size: str) -> float:
        """Seedream 有免费额度，超出后按量付费"""
        return 0.0  # 通常有免费额度
