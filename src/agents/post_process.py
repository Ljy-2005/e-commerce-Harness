"""图像后处理员 — 去背景 + 增强，纯本地处理无 LLM 依赖"""

import asyncio
import base64

from src.agents.base import BaseAgent


class PostProcessAgent(BaseAgent):
    """本地图像处理：去背景（rembg）+ 可选增强，不需要 Provider"""

    meta_name = "图像后处理员"
    timeout_ms = 120_000  # rembg 首次加载模型较慢

    def __init__(self):
        super().__init__(provider=None)  # 纯本地处理，不需要 Provider
        self._rembg_available: bool | None = None

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        images = artifacts.get("images", [])
        if not images:
            return {"processed": 0, "message": "无图片需要处理"}

        # 检查 rembg 可用性
        if self._rembg_available is None:
            self._rembg_available = self._check_rembg()

        processed = []
        for img in images:
            processed_img = dict(img)

            if self._rembg_available:
                try:
                    result = await self._remove_background(img)
                    processed_img.update(result)
                except Exception as e:
                    processed_img["processing_status"] = f"error: {str(e)[:100]}"
            else:
                processed_img["processing_status"] = "skipped (rembg not installed)"
                processed_img["processing_hint"] = (
                    "pip install rembg 启用自动去背景"
                )

            processed.append(processed_img)

        return {
            "images": processed,
            "processed_count": len(processed),
            "rembg_available": self._rembg_available,
        }

    async def _remove_background(self, img: dict) -> dict:
        """使用 rembg 去除图片背景，返回 base64 PNG"""
        b64_data = img.get("base64_data", "")
        if not b64_data or len(b64_data) < 100:
            return {"processing_status": "skipped (no valid base64 data)"}

        # 解码 base64
        raw = base64.b64decode(b64_data)

        # rembg 处理（在线程池中运行，避免阻塞事件循环）
        loop = asyncio.get_running_loop()
        output_bytes = await loop.run_in_executor(None, self._rembg_remove, raw)

        # 编码回 base64
        output_b64 = base64.b64encode(output_bytes).decode("utf-8")

        return {
            "base64_data": output_b64,
            "processing_status": "background_removed",
            "original_size": len(raw),
            "processed_size": len(output_bytes),
        }

    def _rembg_remove(self, raw_bytes: bytes) -> bytes:
        """同步执行 rembg（由 run_in_executor 调用）"""
        from rembg import remove
        return remove(raw_bytes)

    def _check_rembg(self) -> bool:
        """检测 rembg 是否可用"""
        try:
            import rembg  # noqa: F401
            return True
        except ImportError:
            return False
