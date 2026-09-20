"""生图员 — 调用 Image API 生成商品图

## 历史事故与演进

- A30：方舟 Seedream 5.0 要求图像 ≥ 3,686,400 像素，而这里写死 `size="1024x1024"` →
  上游 400 InvalidParameter，且 `result["error"]` 从不检查 → 记下 3 条空图当成功。
  现状：尺寸按「config/image.yaml → models.yaml → 路由默认（ark=2048x2048）」解析，
  Provider 报错立即上报，单张调用有独立超时。

- 本轮（用户三点反馈）：
  1. **文+图双条件**：参考图（上传的真实包装）必须与文本提示词**同时**送进生图接口
     —— 此前只送文本，模型没见过包装，于是把 `DEFOEBUENA®` 编成了 `NUTRIVA®`；
  2. **按套图槽位出图**：此前只取 `main_image.prompt` 连打 `variants` 次，产出的是
     "同一张图的三个候选"，不是可上传的套图；
  3. **文字策略**：包装文字要么逐字保留（靠参考图，不靠文字描述）、要么干净虚化交后期贴图，
     绝不生成文字；**没有可用参考图时强制虚化**（纯文生图必然编造文字）。
"""

import asyncio

from src.agents.base import BaseAgent
from src.core.config import image_options, resolve_image_size
from src.harness.image_prompt import build_image_prompt
from src.harness.reference_images import collect_references, reference_sources
from src.harness.set_plan import normalize_set_plan, platform_slot_plan

# 单张出图的调用上限：实测方舟 2048×2048 约 30–50s，留 2 倍余量。
# Agent 自身的 timeout_ms 是整轮（多张）兜底，这里防"单张挂死吃掉整轮预算"。
PER_IMAGE_TIMEOUT_S = 120.0
# 整轮预算：`max(配置的下限, 张数 × 单张预算 + 收尾余量)`。
# 实测 6 张串行 303s；补上详情图 10 张 ≈505s，若按固定 420s 会整轮超时，
# 而 `artifacts["images"]` 是整键替换 → 已出的图会一起丢（A74）。
PER_IMAGE_BUDGET_MS = 90_000
BUDGET_HEADROOM_MS = 60_000
BUDGET_CEILING_MS = 20 * 60 * 1000
# 落盘/展示的提示词全文上限（改前是 200 字截断，用户根本看不到提示词）
MAX_PROMPT_RECORD_CHARS = 4000


class ImageGeneratorAgent(BaseAgent):
    """使用 Image 能力，将提示词转化为图片"""

    meta_name = "生图员"
    timeout_ms = 420_000

    def __init__(self, provider=None):
        super().__init__(provider=provider)
        self._generate_params: set[str] | None | bool = False   # False = 尚未探测
        self._planned_jobs = 0                                  # 本次计划的张数（超时预算用）

    def timeout_budget(self, session) -> int:
        """整轮预算：张数决定（实测单张约 50s，见模块常量注释）"""
        base = int(self.timeout_ms)
        jobs = int(getattr(self, "_planned_jobs", 0) or 0)
        if jobs <= 0:
            return base
        return min(BUDGET_CEILING_MS, max(base, jobs * PER_IMAGE_BUDGET_MS + BUDGET_HEADROOM_MS))

    def _accepted_kwargs(self) -> set[str] | None:
        """探测 Provider 的 `generate()` 接受哪些关键字（`None` = 接受任意 `**kwargs`）

        为什么要探测：图像 Provider 是**可插拔**的（用户可自定义服务商）。本轮给
        `generate()` 加了 `reference_images` / `options` 两个关键字，旧签名的自定义
        Provider 会直接 TypeError —— 这里按签名过滤，旧 Provider 自动降级，
        并把"参考图被忽略"如实记进产物（而不是抛一个看不懂的错）。
        """
        if self._generate_params is not False:
            return self._generate_params  # type: ignore[return-value]
        import inspect
        try:
            parameters = inspect.signature(self.provider.generate).parameters
        except (TypeError, ValueError, AttributeError):
            self._generate_params = set()
            return self._generate_params
        if any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
            self._generate_params = None
        else:
            self._generate_params = {name for name, p in parameters.items()
                                     if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
        return self._generate_params

    def _generate_kwargs(self, job: dict, size: str, references: list[str],
                         provider_options: dict) -> tuple[dict, list[str]]:
        """组装调用实参；返回 `(kwargs, 因签名不支持而被忽略的键)`"""
        wanted: dict = {
            "prompt": job.get("_prompt", ""),
            "negative_prompt": job.get("negative_prompt", ""),
            "size": size,
            **self._model_kwargs(),
            "reference_images": references or None,
            "options": provider_options,
        }
        accepted = self._accepted_kwargs()
        if accepted is None:
            return wanted, []
        ignored = sorted(key for key in wanted if key not in accepted)
        return {key: value for key, value in wanted.items() if key in accepted}, ignored

    async def _execute_impl(self, task_brief: str, session) -> dict:
        artifacts = session.get("artifacts", {})
        prompts = artifacts.get("prompts", {}) or {}
        task = session.get("task", {}) or {}
        platform = task.get("platform", "")

        options = image_options()
        size = resolve_image_size(getattr(self.provider, "default_size", ""))
        mock = self._is_mock_provider()

        # ── 参考图（"文+图"里的"图"）──
        references, reference_notes = self._prepare_references(session, options)
        # ── 文字策略：没有可靠文字来源时强制虚化 ──
        text_strategy, strategy_notes = self._resolve_text_strategy(options, references)
        # ── 出图任务清单：有套图编排就按槽位出图，否则走旧的单图多候选 ──
        identity = artifacts.get("product_identity") or {}
        jobs = self._build_jobs(prompts, options, platform, identity=identity,
                               slot_override=(task or {}).get("slot_override"))
        if not jobs:
            return {"error": "生图失败：既没有套图编排（set_plan），也没有可用提示词"}
        self._planned_jobs = len(jobs)   # 超时预算按实际张数（A74）

        provider_options = {"watermark": bool(options["watermark"])}
        images: list[dict] = []
        total_cost = 0.0
        cost_known = False       # 至少一张图拿到了已标定价格
        cost_unknown = False     # 有图的价格未标定（不能把 0 当成"没花钱"）
        unknown_images = 0       # 未标定价格的**张数**（用量是事实，要能显示"另有 N 张未标定"）
        model_used = ""

        for job in jobs:
            prompt, prompt_notes = self._compose_prompt(job, text_strategy, references)
            job["_prompt"] = prompt
            job["_prompt_notes"] = prompt_notes
            if mock:
                record = self._mock_image(job, prompt, text_strategy, references)
                self._compose_info_slot(job, record, artifacts, task)
                images.append(record)
                continue

            call_kwargs, signature_ignored = self._generate_kwargs(
                job, size, references, provider_options)
            if signature_ignored:
                # 旧签名 Provider：明确记下"哪些条件没送上去"，不让它静默降级
                for key in signature_ignored:
                    note = f"Provider 不支持参数 {key}，已忽略（{job['name']}）"
                    if note not in strategy_notes:
                        strategy_notes.append(note)

            try:
                result = await asyncio.wait_for(
                    self.provider.generate(**call_kwargs),
                    timeout=PER_IMAGE_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                return {"error": f"生图失败（{job['name']}）：单张超过 {PER_IMAGE_TIMEOUT_S:.0f}s 未返回"}

            # 故障优先上报：把 Provider 的错误原样交给引擎（引擎据此标记 error、
            # 保留既有产物、写 error_history），绝不落成"空图 + 成功"
            if isinstance(result, dict) and result.get("error"):
                return {"error": f"生图失败（{job['name']}）：{result['error']}"}

            image_url = str(result.get("image_url") or "")
            base64_data = str(result.get("base64_data") or "")
            if not image_url and not base64_data:
                return {"error": f"生图失败（{job['name']}）：上游未返回图像数据（无 URL / base64）"}

            unit_cost = result.get("cost_usd")
            if unit_cost is None:
                # 价格未标定（方舟 Seedream 等）：张数照记，金额不猜 —— 此前 `or 0.0`
                # 会把"未知"变成 $0.00 汇总回去
                cost_unknown = True
                unknown_images += 1
            else:
                total_cost += float(unit_cost)
                cost_known = True
            model_used = result.get("model_used") or model_used or self._get_model()
            record = self._image_record(job, prompt, image_url, base64_data, result,
                                        text_strategy, references, options, size,
                                        signature_ignored)
            # 信息类槽位（卖点/成分/人群/功效/规格/用法/资质）：模型只出**无字底图**，
            # 文字层由本地排版绘制（字形准确、事实可控、不额外花钱）
            self._compose_info_slot(job, record, artifacts, task)
            images.append(record)

        payload = {"images": images}
        if reference_notes or strategy_notes:
            payload["image_notes"] = reference_notes + strategy_notes
        if not mock:
            # 回传用量信封：_track_cost 依赖 cost_usd/model_used 才能记上生图费用（A38）
            # 价格未标定时 cost_usd=None（**不是 0.0**）并标 cost_unknown，界面显示"未标定"
            payload["cost_usd"] = round(total_cost, 6) if cost_known else None
            payload["cost_unknown"] = cost_unknown or not cost_known
            payload["cost_unknown_images"] = unknown_images
            payload["model_used"] = model_used or self._get_model()
        return payload

    # ── 条件准备 ──

    def _prepare_references(self, session, options) -> tuple[list[str], list[str]]:
        if options["reference_mode"] == "off":
            return [], ["reference_mode=off：按要求强制纯文生图（不做文+图对照实验）"]
        references, notes = collect_references(reference_sources(session),
                                               limit=int(options["max_references"]))
        if references:
            notes.insert(0, f"已启用文+图双条件：{len(references)} 张真实商品图作为参考图")
        return references, notes

    def _resolve_text_strategy(self, options, references) -> tuple[str, list[str]]:
        strategy = str(options["text_strategy"])
        notes: list[str] = []
        if strategy == "preserve" and not references:
            # 纯文生图必然编造包装文字（实测把 DEFOEBUENA® 编成 NUTRIVA®）→ 强制虚化
            strategy = "blur"
            notes.append("没有可用的参考图，文字策略自动降级为 blur（不让模型编造包装文字）")
        return strategy, notes

    def _build_jobs(self, prompts: dict, options: dict, platform, *,
                    identity: dict | None = None,
                    slot_override: list[str] | None = None) -> list[dict]:
        """出图任务清单：套图槽位优先，其次旧的单图多候选

        每个 job 带**逐张契约**（number/intent/design/must/avoid/keep_clear/palette）——
        用户反馈"未有明确约束每一张该有的提示词"，契约由 `config/platforms.yaml` 提供、
        最终提示词由 `harness/image_prompt.build_image_prompt` 组装成六段式。

        `slot_override`（会话级槽位子集，来自 `POST /api/sessions` 的 `slots`）优先于
        `config/image.yaml` 的平台槽位覆盖 —— 用于最小付费冒烟（只跑 2 张）与"只重出某几张"。
        """
        configured = (options.get("platforms") or {}).get(str(platform) or "", {}).get("slots") \
            if isinstance(options.get("platforms"), dict) else None
        subset = [str(item).strip() for item in (slot_override or []) if str(item or "").strip()]
        override = subset or configured
        palette = (identity or {}).get("brand_palette") if isinstance(identity, dict) else None
        plan = normalize_set_plan(prompts, platform=platform, palette=palette,
                                  slot_override=platform_slot_plan(platform, override),
                                  only_slots=subset)
        candidates = int(options["slot_candidates"])
        if plan:
            jobs: list[dict] = []
            for slot in plan["slots"]:
                for index in range(candidates):
                    jobs.append({
                        "name": slot["slot_id"] if candidates == 1
                        else f"{slot['slot_id']}#{index + 1}",
                        "number": slot.get("number", 0),
                        "slot_id": slot["slot_id"],
                        "role": slot.get("role", ""),
                        "prompt": slot["prompt"],
                        "negative_prompt": slot.get("negative_prompt", ""),
                        "composition": slot.get("composition", ""),
                        "background": slot.get("background", ""),
                        "aspect": slot.get("aspect", ""),
                        "text_in_image": bool(slot.get("text_in_image")),
                        # 产出方式来自槽位目录：photo=模型直接出成品｜info=底图+本地排版
                        "kind": slot.get("kind", "photo"),
                        "layout": slot.get("layout", ""),
                        "usage": slot.get("usage", "main"),
                        # 逐张契约（模型与审核员都改不掉）
                        "intent": slot.get("intent", ""),
                        "design": slot.get("design", ""),
                        "must": slot.get("must") or [],
                        "avoid": slot.get("avoid") or [],
                        "keep_clear": slot.get("keep_clear", ""),
                        "bg_policy": slot.get("bg_policy", ""),
                        "palette": slot.get("palette") or (palette or {}),
                        "identity": identity or {},
                        "revised_by_reviewer": bool(slot.get("revised_by_reviewer")),
                        "variant": index + 1,
                    })
            return jobs

        # 旧路径（无套图编排）：同一提示词出 variants 张候选，保持历史行为
        main_prompt = ""
        if isinstance(prompts.get("main_image"), dict):
            main_prompt = str(prompts["main_image"].get("prompt") or "")
        if not main_prompt:
            for scene in prompts.get("scene_images", []) or []:
                if isinstance(scene, dict) and scene.get("prompt"):
                    main_prompt = str(scene["prompt"])
                    break
        if not main_prompt:
            main_prompt = "E-commerce product photo, white background, professional lighting"
        return [{"name": f"variant_{i + 1}", "number": 0, "slot_id": "", "role": "主图",
                 "prompt": main_prompt, "negative_prompt": "", "composition": "",
                 "background": "", "aspect": "", "text_in_image": False,
                 "kind": "photo", "usage": "main", "must": [], "avoid": [],
                 "keep_clear": "", "palette": palette or {}, "identity": identity or {},
                 "variant": i + 1}
                for i in range(int(options["variants"]))]

    def _compose_prompt(self, job: dict, text_strategy: str,
                        references: list[str]) -> tuple[str, list[str]]:
        """组装最终生图提示词（六段式，见 `harness/image_prompt.build_image_prompt`）

        返回 `(提示词, 说明)`：说明里记录身份词移除、画面超长裁剪等（不静默）。
        """
        return build_image_prompt(
            number=int(job.get("number") or 0),
            slot_id=job.get("slot_id", ""),
            role=job.get("role", ""),
            usage=job.get("usage", ""),
            kind=job.get("kind", "photo"),
            aspect=job.get("aspect", ""),
            scene=str(job.get("prompt") or ""),
            composition=job.get("composition", ""),
            background=job.get("background", ""),
            must=job.get("must") or [],
            avoid=job.get("avoid") or [],
            keep_clear=job.get("keep_clear", ""),
            palette=job.get("palette") or {},
            identity=job.get("identity") or {},
            text_strategy=text_strategy,
            has_references=bool(references),
            text_in_image=bool(job.get("text_in_image")),
        )

    # ── 信息类槽位：本地排版文字层 ──

    def _compose_info_slot(self, job: dict, record: dict, artifacts: dict,
                           task: dict | None = None) -> None:
        """给信息类槽位叠加本地排版的文字层（原地修改 `record`）

        - `kind != "info"`（纯摄影槽位）→ 不动；
        - 文案缺事实依据 / 命中违禁词 → `text_status="blocked"` + 可读原因，
          **不产出一张编造的信息图**（前端显示"待补素材"）；
        - 用户补充的事实（`task.product_facts`，来自会话页"补充事实"）优先用于排版；
        - 成功 → 用合成后的图替换 `record` 的图源，并保留无字底图 URL（24h 内可重排）。
        """
        if str(job.get("kind") or "photo") != "info":
            return
        from src.harness.image_compose import compose_info_image
        from src.harness.slot_copy import build_slot_copy

        copy = build_slot_copy(job["slot_id"],
                               identity=(artifacts or {}).get("product_identity"),
                               analysis=(artifacts or {}).get("analysis"),
                               user_copy=(task or {}).get("product_facts"))
        record["slot_copy"] = {"title": copy.get("title"), "items": copy.get("items"),
                              "footer": copy.get("footer"), "sources": copy.get("sources"),
                              "reason": copy.get("reason")}
        if copy.get("blocked"):
            record["text_status"] = "blocked"
            record["text_reason"] = copy.get("reason") or "文案缺少事实依据"
            record["processing_status"] = f"blocked: {record['text_reason']}"
            return

        base_bytes = b""
        if record.get("base64_data"):
            try:
                import base64 as _b64
                base_bytes = _b64.b64decode(record["base64_data"], validate=False)
            except Exception:  # noqa: BLE001 — 解不出就走纯色版式底
                base_bytes = b""
        typography = self._typography_with_palette(artifacts)
        composed, meta = compose_info_image(base_bytes or None, copy, job,
                                            typography=typography)
        if not meta.get("ok") or not composed:
            record["text_status"] = "failed"
            record["text_reason"] = meta.get("reason") or "本地排版失败"
            return
        import base64 as _b64

        record["base_image_url"] = record.get("image_url", "")   # 无字底图（可据此重排）
        record["base64_data"] = _b64.b64encode(composed).decode()
        record["image_url"] = ""                                  # 交付物以本地合成结果为准
        record["processing_status"] = "info_composed"
        record["text_status"] = "composed"
        record["compose"] = meta
        params = record.setdefault("generation_params", {})
        params["kind"] = "info"
        params["layout"] = meta.get("layout")
        params["text_items"] = meta.get("items")
        params["text_font"] = meta.get("font")

    # ── 记录 ──

    def _typography_with_palette(self, artifacts: dict) -> dict:
        """排版样式：**默认取包装品牌色**（用户没自定义主/辅色时），让图文两层同一色系

        "正规感"最便宜的一招：画面与文字层共用包装上的真实色系。
        用户在设置页显式改过 `typography.brand_color/accent_color` → 以用户为准。
        """
        from src.core.config import DEFAULT_TYPOGRAPHY
        typography = dict(image_options().get("typography") or {})
        palette = ((artifacts or {}).get("product_identity") or {}).get("brand_palette") or {}
        if not isinstance(palette, dict):
            return typography
        primary = str(palette.get("primary") or "").strip()
        secondary = str(palette.get("secondary") or "").strip()
        default_brand = DEFAULT_TYPOGRAPHY["brand_color"].upper()
        default_accent = DEFAULT_TYPOGRAPHY["accent_color"].upper()
        if primary and str(typography.get("brand_color") or "").upper() == default_brand:
            typography["brand_color"] = primary
        if secondary and str(typography.get("accent_color") or "").upper() == default_accent:
            typography["accent_color"] = secondary
        return typography

    def _image_record(self, job: dict, prompt: str, image_url: str, base64_data: str,
                      result: dict, text_strategy: str, references: list[str],
                      options: dict, size: str, signature_ignored: list[str] | None = None) -> dict:
        ignored = list(result.get("ignored_params") or []) + list(signature_ignored or [])
        return {
            "prompt_name": job["name"],
            "slot_id": job.get("slot_id", ""),
            "slot_role": job.get("role", ""),
            # 提示词**全文**入库（改前 `prompt[:200]` 截断，用户看不到自己这套图是怎么写的）
            "prompt_text": prompt[:MAX_PROMPT_RECORD_CHARS],
            "prompt_number": job.get("number") or 0,
            "prompt_sections": {
                "intent": job.get("intent", ""),
                "design": job.get("design", ""),
                "must": job.get("must") or [],
                "avoid": job.get("avoid") or [],
                "keep_clear": job.get("keep_clear", ""),
            },
            "prompt_notes": job.get("_prompt_notes") or [],
            "revised_by_reviewer": bool(job.get("revised_by_reviewer")),
            "image_url": image_url,
            "base64_data": base64_data,
            "model_used": result.get("model_used") or getattr(self.provider, "name", ""),
            # 生图参数全量入产物/审计：复盘"这张图到底怎么生成的"（含是否真的用了参考图）
            "generation_params": {
                "size": size,
                "model": result.get("model_used") or self._get_model(),
                "slot_id": job.get("slot_id", ""),
                "text_strategy": text_strategy,
                "reference_count": int(result.get("reference_count")
                                       if result.get("reference_count") is not None
                                       else len(references)),
                "watermark": bool(options["watermark"]),
                "ignored_params": ignored,
                # 设计方向与背景策略入档：复盘"这张图按哪套规范出的"
                "bg_policy": job.get("bg_policy", ""),
                "brand_palette": job.get("palette") or {},
                "prompt_chars": len(prompt or ""),
                **(result.get("request_params") or {}),
            },
            "processing_status": "raw",
        }

    def _is_mock_provider(self) -> bool:
        from src.providers.mock import MockImageProvider
        return not self.provider or isinstance(self.provider, MockImageProvider)

    def _mock_image(self, job: dict, prompt: str, text_strategy: str,
                    references: list[str]) -> dict:
        from src.providers.mock import MockImageProvider
        mock = MockImageProvider()
        result = mock._make_placeholder(f"商品图 {job['name']}: {prompt[:30]}...", "800x800")
        import base64
        b64 = base64.b64encode(result.encode()).decode()
        return {
            "prompt_name": job["name"],
            "slot_id": job.get("slot_id", ""),
            "slot_role": job.get("role", ""),
            "prompt_text": prompt[:MAX_PROMPT_RECORD_CHARS],
            "prompt_number": job.get("number") or 0,
            "prompt_sections": {
                "intent": job.get("intent", ""),
                "design": job.get("design", ""),
                "must": job.get("must") or [],
                "avoid": job.get("avoid") or [],
                "keep_clear": job.get("keep_clear", ""),
            },
            "prompt_notes": job.get("_prompt_notes") or [],
            "revised_by_reviewer": bool(job.get("revised_by_reviewer")),
            "image_url": f"data:image/svg+xml;base64,{b64}",
            "base64_data": b64,
            "model_used": "mock/svg-placeholder",
            "generation_params": {"size": "800x800", "slot_id": job.get("slot_id", ""),
                                  "text_strategy": text_strategy,
                                  "reference_count": len(references), "model": "mock",
                                  "bg_policy": job.get("bg_policy", ""),
                                  "prompt_chars": len(prompt or "")},
            "processing_status": "raw",
        }
