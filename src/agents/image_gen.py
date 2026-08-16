"""生图员 — 调用 Image API 生成商品图"""

from src.agents.base import BaseAgent


class ImageGeneratorAgent(BaseAgent):
    """使用 Image 能力，将提示词转化为图片"""

    meta_name = "生图员"
    timeout_ms = 60_000

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        prompts = artifacts.get("prompts", {})

        # 提取主图提示词
        main_prompt = ""
        if prompts.get("main_image"):
            main_prompt = prompts["main_image"].get("prompt", "")
        if not main_prompt:
            for scene in prompts.get("scene_images", []):
                if scene.get("prompt"):
                    main_prompt = scene["prompt"]
                    break
        if not main_prompt:
            main_prompt = "E-commerce product photo, white background, professional lighting"

        images = []
        # 生成 3 张变体
        from src.providers.mock import MockImageProvider
        for i in range(3):
            if self.provider and not isinstance(self.provider, MockImageProvider):
                result = await self.provider.generate(
                    prompt=main_prompt,
                    size="1024x1024",
                    **self._model_kwargs(),
                )
                images.append({
                    "prompt_name": f"variant_{i+1}",
                    "prompt_text": main_prompt[:200],
                    "image_url": result.get("image_url", ""),
                    "base64_data": result.get("base64_data", ""),
                    "model_used": result.get("model_used", self.provider.name),
                    "generation_params": {"size": "1024x1024"},
                    "processing_status": "raw",
                })
            else:
                images.append(self._mock_image(i + 1, main_prompt))

        return {"images": images}

    def _mock_image(self, index: int, prompt: str) -> dict:
        from src.providers.mock import MockImageProvider
        mock = MockImageProvider()
        result = mock._make_placeholder(f"商品图 #{index}: {prompt[:30]}...", "800x800")
        import base64
        b64 = base64.b64encode(result.encode()).decode()
        return {
            "prompt_name": f"variant_{index}",
            "prompt_text": prompt[:200],
            "image_url": f"data:image/svg+xml;base64,{b64}",
            "base64_data": b64,
            "model_used": "mock/svg-placeholder",
            "generation_params": {"size": "800x800"},
            "processing_status": "raw",
        }
