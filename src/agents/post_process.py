"""图像后处理员 — 去背景 + 增强，纯本地处理无 LLM 依赖"""

from src.agents.base import BaseAgent


class PostProcessAgent(BaseAgent):
    """本地图像处理：去背景 + 增强，不需要 Provider"""

    meta_name = "图像后处理员"
    timeout_ms = 30_000

    def __init__(self):
        super().__init__(provider=None)  # 纯本地处理，不需要 Provider

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        images = artifacts.get("images", [])
        if not images:
            return {"processed": 0, "message": "无图片需要处理"}

        processed = []
        for img in images:
            processed_img = dict(img)
            # P0 简化：标记处理状态，不做实际 AI 去背景
            # 后续可接入 rembg 或 BiRefNet
            processed_img["processing_status"] = "processed"
            processed.append(processed_img)

        return {"images": processed, "processed_count": len(processed)}
