"""FLUX.1 Provider — 写实光影最强的图像生成 (Replicate / Fal.ai / BFL)"""

import asyncio
import os
from src.providers.base import BaseImageProvider


class FluxImageProvider(BaseImageProvider):
    """FLUX.1 — 支持三种后端: Replicate / Fal.ai / BFL 官方"""

    name = "flux"
    capabilities = ["image"]

    def __init__(self):
        self.replicate_key = os.getenv("REPLICATE_API_KEY", "")
        self.fal_key = os.getenv("FAL_KEY", "")
        self.bfl_key = os.getenv("BFL_API_KEY", "")

        # 优先级：BFL > Fal.ai > Replicate
        if self.bfl_key:
            self._backend = "bfl"
        elif self.fal_key:
            self._backend = "fal"
        elif self.replicate_key:
            self._backend = "replicate"
        else:
            self._backend = None

    async def generate(
        self, prompt: str, negative_prompt: str = "", size: str = "1024x1024", model: str = "flux.1-dev"
    ) -> dict:
        if self._backend == "bfl":
            return await self._via_bfl(prompt, size, model)
        elif self._backend == "fal":
            return await self._via_fal(prompt, negative_prompt, size)
        elif self._backend == "replicate":
            return await self._via_replicate(prompt, negative_prompt, size, model)
        else:
            return {
                "error": "FLUX 需要 BFL_API_KEY 或 FAL_KEY 或 REPLICATE_API_KEY",
                "hint": "推荐: https://api.bfl.ml (BFL 官方, $0.05/张) 或 https://fal.ai",
            }

    async def _via_bfl(self, prompt: str, size: str, model: str) -> dict:
        import httpx
        headers = {"x-key": self.bfl_key, "Content-Type": "application/json"}

        w, h = self._parse_size(size)
        body = {
            "prompt": prompt,
            "width": w,
            "height": h,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.bfl.ml/v1/flux-pro-1.1",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"BFL API error: {resp.status_code}", "detail": resp.text[:500]}

            data = resp.json()
            return {
                "image_url": data.get("result", {}).get("sample", ""),
                "base64_data": "",
                "model_used": "flux-pro-1.1",
                "cost_usd": 0.05,
            }

    async def _via_fal(self, prompt: str, negative_prompt: str, size: str) -> dict:
        import httpx
        headers = {"Authorization": f"Key {self.fal_key}", "Content-Type": "application/json"}

        # 映射像素尺寸到 Fal.ai 预设
        size_map = {
            "1024x1024": "square_hd", "1024x1792": "portrait_4_3",
            "1792x1024": "landscape_16_9", "768x768": "square",
        }
        fal_size = size if size in ("square_hd", "portrait_4_3", "landscape_16_9", "square") else size_map.get(size, "square_hd")
        body = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or "blurry, low quality",
            "image_size": fal_size,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://fal.run/fal-ai/flux/dev",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                return {"error": f"Fal.ai API error: {resp.status_code}", "detail": resp.text[:500]}

            data = resp.json()
            images = data.get("images", [])
            return {
                "image_url": images[0].get("url", "") if images else "",
                "base64_data": "",
                "model_used": "flux.1-dev",
                "cost_usd": 0.05,
            }

    async def _via_replicate(self, prompt: str, negative_prompt: str, size: str, model: str = "flux.1-dev") -> dict:
        import httpx
        headers = {"Authorization": f"Token {self.replicate_key}", "Content-Type": "application/json"}

        model_version = model if model != "flux.1-dev" else "black-forest-labs/flux-dev"
        body = {
            "version": model_version,
            "input": {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": self._parse_size(size)[0],
                "height": self._parse_size(size)[1],
            },
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.replicate.com/v1/predictions",
                headers=headers,
                json=body,
            )
            if resp.status_code not in (200, 201):
                return {"error": f"Replicate API error: {resp.status_code}", "detail": resp.text[:500]}

            data = resp.json()
            prediction_id = data.get("id", "")

            # 轮询等待结果
            for _ in range(30):
                await client.get(
                    f"https://api.replicate.com/v1/predictions/{prediction_id}",
                    headers=headers,
                )
                status_resp = await client.get(
                    f"https://api.replicate.com/v1/predictions/{prediction_id}",
                    headers=headers,
                )
                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    if status_data.get("status") == "succeeded":
                        output = status_data.get("output", [])
                        return {
                            "image_url": output[0] if output else "",
                            "base64_data": "",
                            "model_used": "flux-dev",
                            "cost_usd": 0.05,
                        }
                    elif status_data.get("status") == "failed":
                        return {"error": "Replicate prediction failed"}

                await asyncio.sleep(3)

            return {"error": "Replicate prediction timed out"}

    def _parse_size(self, size: str) -> tuple[int, int]:
        if "x" in size:
            parts = size.split("x")
            return int(parts[0]), int(parts[1])
        return 1024, 1024
