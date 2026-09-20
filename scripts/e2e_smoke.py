"""E2E 冒烟脚本（测试计划 P2）— 一键验证全链路

场景：
  S1 群聊全链路   上传图 → 会话 → 8 轮群聊 → completed + 全部产出物
  S2 工作流       实例化 approval_matrix → 自动挡跑完 → SLA 矩阵自动审批事件
  S3 批量         JSON 5 项（1 坏项死信）→ 计数精确 → 报表 → retry_failed 重跑
  S4 记忆/审计    审计条目含 tenant_id；记忆库统计可用
  S5 设置闭环     保存模型映射 → 重载生效 → 还原
  S6 鉴权冒烟     租户 Key 创建→无Key 401→错Key 401→绑定生效→管理面 403→轮换→删除
  S7 前端冒烟     首页 200 + root 挂载 + 前端代理 /health 通

用法：
  python scripts/e2e_smoke.py                    # 自动启动前后端（Mock 模式），跑全部场景
  python scripts/e2e_smoke.py --existing         # 复用已在运行的服务（不会停止/清理它）
  python scripts/e2e_smoke.py --scenarios S1,S2  # 只跑指定场景
  python scripts/e2e_smoke.py --real             # 允许真实模式（超时放宽，S1 产出物按 Mock 断言可能不稳）
  python scripts/e2e_smoke.py --existing --base-url http://127.0.0.1:8002 --frontend-url http://localhost:5174

端口语义（第三轮审计 B2-17）：
  自动模式要求 8000/5173 空闲，否则明确拒绝（不"认领"别人已在跑的服务）；
  自定义端口只支持 --existing 复用；收尾只清理本次自己启动的端口。

退出码：0 全部通过；1 有失败；2 前置条件不满足（如真实模式未加 --real）
"""

import argparse
import base64
import io
import json
import os
import re
import socket
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

PASS, FAIL = "✅ PASS", "❌ FAIL"

# 自动启动模式的默认端口（vite 代理硬编码后端 8000，见 frontend/vite.config.js）
_DEFAULT_BACKEND_PORT, _DEFAULT_FRONTEND_PORT = 8000, 5173

# Windows GBK 控制台兜底：强制 UTF-8 输出（emoji/中文）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def make_test_image(size: int = 64) -> bytes:
    """生成一张合法小 JPEG（Pillow，纯内存）"""
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (120, 90, 200)).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def b64_image() -> str:
    return base64.b64encode(make_test_image()).decode("utf-8")


# 影响 Mock 确定性的 Provider Key（自动启动时清空，防 secrets.yaml 注入真实 Key）
_PROVIDER_KEY_ENVS = [
    "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "SEEDREAM_API_KEY",
    "VOLCANO_ACCESS_KEY", "VOLCANO_SECRET_KEY", "DASHSCOPE_API_KEY",
    "BFL_API_KEY", "FAL_KEY", "REPLICATE_API_KEY",
]


def _provider_key_envs() -> list[str]:
    """需要清空的凭据变量 = 硬编码清单 ∪ **config/secrets.yaml 里的全部键**

    实测踩坑：硬编码清单漏了后加的 `ARK_API_KEY`（方舟路由是后来才有的），
    于是用户保存过方舟 Key 后，`secrets.yaml` 会把真实 Key 注入子进程 →
    Mock 确定性预检直接拒绝运行（预检本身是对的，但脚本就再也跑不起来了）。

    直接从注入源（secrets.yaml）取键名，之后无论再加多少服务商都不会漏。
    """
    envs = set(_PROVIDER_KEY_ENVS)
    try:
        secrets_file = Path(__file__).resolve().parent.parent / "config" / "secrets.yaml"
        if secrets_file.exists():
            import yaml
            data = yaml.safe_load(secrets_file.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                envs.update(str(key) for key in data)
    except Exception:  # noqa: BLE001 — 取不到就退回硬编码清单
        pass
    return sorted(envs)

ADMIN_KEY_ENV = "ECOMM_API_KEY"


class E2E:
    def __init__(self, base_url: str, frontend_url: str, timeout_s: int, admin_key: str = ""):
        self.admin_key = admin_key
        headers = {"X-API-Key": admin_key} if admin_key else {}
        self.c = httpx.Client(base_url=base_url, timeout=90, headers=headers)
        self.frontend = frontend_url
        self.timeout = timeout_s
        self.results: list[tuple[str, bool, str]] = []
        self.created_sessions: list[str] = []
        self.tenant_keys_file = ROOT / "config" / "tenant_keys.yaml"

    # ── 工具 ──

    def check(self, name: str, ok: bool, detail: str = ""):
        """实时打印单条断言结果（场景级计分在 run() 汇总）"""
        print(f"  {PASS if ok else FAIL} {name}" + (f" — {detail}" if detail else ""))
        return ok

    def poll(self, fn, terminal, label: str, timeout_s: int | None = None):
        deadline = time.monotonic() + (timeout_s or self.timeout)
        while time.monotonic() < deadline:
            data = fn()
            if terminal(data):
                return data
            time.sleep(1)
        raise AssertionError(f"等待{label}超时（{timeout_s or self.timeout}s），最后状态: {json.dumps(data, ensure_ascii=False)[:200]}")

    def cleanup_sessions(self):
        for sid in self.created_sessions:
            try:
                self.c.delete(f"/api/sessions/{sid}")
            except Exception:
                pass

    # ── S1 群聊全链路 ──

    def s1_group_chat(self):
        r = self.c.post("/api/sessions", files={"files": ("smoke.jpg", make_test_image(), "image/jpeg")},
                        data={"product_info": "E2E 冒烟测试商品", "platform": "taobao", "mode": "serial"})
        self.check("S1 创建会话", r.status_code in (200, 201), f"HTTP {r.status_code}")
        sid = r.json()["session_id"]
        self.created_sessions.append(sid)

        d = self.poll(lambda: self.c.get(f"/api/sessions/{sid}").json(),
                      lambda s: s["status"] in ("completed", "failed"), "会话完成")
        self.check("S1 会话完成", d["status"] == "completed", f"status={d['status']} turns={d.get('turn_count')}")
        arts = d.get("artifacts", {})
        missing = [k for k in ("analysis", "prompts", "images", "review", "compliance") if k not in arts]
        self.check("S1 产出物齐全", not missing, f"missing={missing or '无'}")
        self.check("S1 轮次充足", d.get("turn_count", 0) >= 6, f"turns={d.get('turn_count')}")

        # ── 本轮新增的交付物契约（用户三点反馈） ──
        # ① 商品身份卡：品牌/品名必须被识别并**醒目提醒**（未确认时也不得让模型编文字）
        identity = arts.get("product_identity") or {}
        self.check("S1 商品身份卡存在",
                   identity.get("status") in ("confirmed", "uncertain") and "source" in identity,
                   f"status={identity.get('status')} source={identity.get('source')}")
        # ② 套图编排 + 覆盖度：交付物是"一整套"，不是"一张图的多个候选"
        coverage = arts.get("set_plan_coverage") or {}
        images = arts.get("images") or []
        self.check("S1 套图编排存在", bool(arts.get("set_plan")),
                   f"slots={len((arts.get('set_plan') or {}).get('slots') or [])}")
        self.check("S1 套图覆盖完整",
                   bool(coverage.get("expected")) and coverage.get("complete") is True,
                   f"coverage={coverage}")
        self.check("S1 每张图带槽位",
                   bool(images) and all(img.get("slot_id") for img in images),
                   f"slots={[img.get('slot_id') for img in images]}")
        # ③ 落盘文件名带槽位（导出的 ZIP 即一套可上传的图）
        names = [str(img.get("saved_path") or "").split("/")[-1] for img in images]
        self.check("S1 落盘名含槽位",
                   bool(names) and all(any(slot in name for slot in
                                           [str(img.get("slot_id")) for img in images])
                                       for name in names),
                   f"names={names[:3]}")
        # ④ 本地体检：客观指标入产物（背景白度/水印/身份相似度）。
        # 注意：Mock 出的是 **SVG 占位图**，Pillow 无法解码 → usable 计数为 0 属预期；
        # 这里断言"体检通道确实跑了并写了结论"，真实图的数值由 Phase 6 真机验证与单测覆盖。
        quality = arts.get("quality_report") or {}
        self.check("S1 本地体检入产物",
                   "count" in quality and "issues" in quality and "white_bg_ok" in quality,
                   f"count={quality.get('count')}（Mock 为 SVG 占位图，无法体检属预期）"
                   f" white_bg_ok={quality.get('white_bg_ok')}")
        # ⑤ 文+图双条件：生图参数里必须记录参考图张数（Mock 下为 0 也要有字段）
        params = (images[0].get("generation_params") or {}) if images else {}
        self.check("S1 生图参数可复盘",
                   "reference_count" in params and "text_strategy" in params,
                   f"params={ {k: params.get(k) for k in ('reference_count', 'text_strategy')} }")
        # ⑥ 提示词阶段（2026-09-18 用户反馈）：体检 + 审美审核都要落进产物；
        #    逐张提示词要能被用户看到（第N张 + 完整文本，不再截 200 字）
        lint = arts.get("prompt_lint") or {}
        review = arts.get("prompt_review") or {}
        self.check("S1 提示词体检入产物",
                   "checked" in lint and "errors" in lint and "digest" in lint,
                   f"checked={lint.get('checked')} errors={len(lint.get('errors') or [])}")
        self.check("S1 提示词审核有结论",
                   review.get("status") in ("reviewed", "mock", "skipped", "disabled", "error"),
                   f"status={review.get('status')} verdict={review.get('verdict')}")
        first_prompt = str((images[0] or {}).get("prompt_text") or "") if images else ""
        self.check("S1 逐张提示词可读",
                   first_prompt.startswith("第") and "【必须】" in first_prompt,
                   f"chars={len(first_prompt)} head={first_prompt[:24]}")
        # ⑦ 风格档案（A79-A96，用户指定的「风格词库」）：逐槽位命中要落进产物，
        #    否则前端"🎨 采用风格档案"整行没数据（Mock 路径也必须算 —— 本轮自查 #9）
        style_refs = (arts.get("prompts") or {}).get("style_refs") or {}
        self.check("S1 风格档案入产物",
                   style_refs.get("enabled") is True and bool(style_refs.get("entries")),
                   f"enabled={style_refs.get('enabled')} entries={len(style_refs.get('entries') or [])}")
        self.check("S1 风格档案逐槽位命中",
                   bool(style_refs.get("slots")) and
                   all(style_refs["slots"].get(slot.get("slot_id"))
                       for slot in (arts.get("set_plan") or {}).get("slots") or []),
                   f"slots={ {k: [i.get('name') for i in v] for k, v in (style_refs.get('slots') or {}).items()} }")
        self.check("S1 风格档案有可读摘要",
                   "采用风格档案" in str(style_refs.get("message") or ""),
                   f"message={style_refs.get('message')}")
        # 一轮会话一个风格词（A97，用户 2026-09-20）：每张**只注入一条**，且要能看到
        # 哪几张"只按槽位契约写"（严格模式下参考套图没覆盖的槽位不塞第二个风格）
        max_picked = max((len(v) for v in (style_refs.get("slots") or {}).values()), default=0)
        self.check("S1 每张只用一种风格",
                   max_picked <= 1 and style_refs.get("strict_single") is not False,
                   f"max_entries={style_refs.get('max_entries')} max_picked={max_picked}")
        # 套图结构（A98，Mock 演示数据也要落进产物，否则前端整行没数据）
        self.check("S1 套图结构入产物",
                   bool(style_refs.get("sequence")) and bool(style_refs.get("coverage")),
                   f"sequence={len(style_refs.get('sequence') or [])} "
                   f"coverage={len(style_refs.get('coverage') or [])}")

    # ── S2 工作流 approval_matrix ──

    def s2_workflow(self):
        r = self.c.post("/api/workflows/templates/approval_matrix/instantiate",
                        files={"files": ("t.jpg", make_test_image(), "image/jpeg")},
                        data={"platform": "taobao", "mode": "auto"})
        self.check("S2 实例化模板", r.status_code in (200, 201), f"HTTP {r.status_code}")
        job_id = r.json()["job_id"]

        d = self.poll(lambda: self.c.get(f"/api/workflows/jobs/{job_id}").json(),
                      lambda j: j.get("status") in ("completed", "failed", "cancelled"), "作业完成")
        self.check("S2 作业终态", d["status"] == "completed", f"status={d['status']}")
        events = d.get("events", [])
        has_sla = any(e.get("event") == "human_sla_timeout" for e in events)
        has_approve = any(e.get("event") in ("human_decided",) and e.get("payload", {}).get("action") == "approve" for e in events)
        steps_ok = [s for s in d.get("steps", []) if s["status"] == "succeeded"]
        self.check("S2 SLA 自动审批事件", has_sla or has_approve,
                   f"events={[e.get('event') for e in events[-6:]]}")
        self.check("S2 步骤执行", len(steps_ok) >= 5, f"succeeded={len(steps_ok)}/{len(d.get('steps', []))}")

    # ── S3 批量 ──

    def s3_batch(self):
        items = [{"product_images": [b64_image()], "platform": "taobao", "product_info": f"商品{i}"} for i in range(4)]
        items.append({"platform": "taobao"})  # 缺 product_images → 必填输入缺失 → 死信
        r = self.c.post("/api/workflows/batches", json={
            "template_name": "white_bg_suite", "items": items, "mode": "auto", "max_concurrency": 3,
        })
        self.check("S3 创建批量", r.status_code in (200, 201), f"HTTP {r.status_code}")
        batch = r.json()
        batch_id = batch["batch_id"]

        def fetch():
            return self.c.get(f"/api/workflows/batches/{batch_id}").json()

        b = self.poll(fetch, lambda x: x.get("status") in ("completed", "failed", "cancelled")
                      or (x.get("done", 0) + x.get("failed", 0) == x.get("total", 0)), "批次完成")
        self.check("S3 计数精确", b["done"] + b["failed"] == b["total"] == 5,
                   f"done={b['done']} failed={b['failed']} total={b['total']}")
        self.check("S3 坏项进死信", b["failed"] == 1, f"failed={b['failed']}")

        rep = self.c.get("/api/workflows/batches/report").json()
        self.check("S3 报表可用", "overview" in rep and "by_template" in rep and "failure_reasons" in rep,
                   f"keys={sorted(rep.keys())}")

        r = self.c.post(f"/api/workflows/batches/{batch_id}/control", json={"action": "retry_failed"})
        self.check("S3 死信重跑指令", r.status_code == 200, f"HTTP {r.status_code}")
        b2 = self.poll(fetch, lambda x: x.get("status") in ("completed", "failed", "cancelled")
                       or (x.get("done", 0) + x.get("failed", 0) == x.get("total", 0)), "重跑后批次终态")
        self.check("S3 重跑后回到终态", b2["done"] + b2["failed"] == b2["total"], f"status={b2['status']}")

    # ── S4 记忆/审计 ──

    def s4_memory_audit(self):
        a = self.c.get("/api/audit").json()
        entries = a.get("entries", [])
        self.check("S4 审计有条目", len(entries) > 0, f"total={a.get('total')}")
        with_tenant = sum(1 for e in entries if "tenant_id" in e)
        self.check("S4 审计含租户字段", with_tenant >= max(1, len(entries) - 2), f"{with_tenant}/{len(entries)}")

        m = self.c.get("/api/memory/stats").json()
        self.check("S4 记忆统计可用", isinstance(m.get("total_entries"), int) and m["total_entries"] >= 0,
                   f"total_entries={m.get('total_entries')}")

    # ── S5 设置闭环 ──

    def s5_settings(self):
        orig = self.c.get("/api/settings").json()["models_config"]
        trial = json.loads(json.dumps(orig))
        trial.setdefault("capabilities", {}).setdefault("text", {})["default"] = "qwen/qwen-max"

        r = self.c.post("/api/settings/models", json=trial)
        self.check("S5 保存模型映射", r.status_code == 200, f"HTTP {r.status_code}")
        cfg = self.c.get("/api/settings").json()["models_config"]
        self.check("S5 重载生效", cfg["capabilities"]["text"]["default"] == "qwen/qwen-max",
                   f"default={cfg['capabilities']['text']['default']}")

        r = self.c.post("/api/settings/models", json=orig)
        self.check("S5 还原配置", r.status_code == 200, f"HTTP {r.status_code}")

    # ── S6 鉴权冒烟（租户 Key 生命周期；需 admin Key，否则跳过） ──

    def s6_auth(self):
        if not self.admin_key:
            print("  ⏭ 跳过 S6（需要 ECOMM_API_KEY 管理 Key 才能执行租户 Key 轮换/删除；其余场景不受影响）")
            self.results.append(("S6", None, "skipped"))
            return

        key1, key2 = "e2e-tenant-key-1234567890", "e2e-tenant-key-rotated-99"
        admin_h = {"X-API-Key": self.admin_key}
        no_h = {"X-API-Key": ""}
        file_existed = self.tenant_keys_file.exists()
        orig_content = self.tenant_keys_file.read_text(encoding="utf-8") if file_existed else None

        try:
            r = self.c.post("/api/settings/tenant-keys", json={"tenant_id": "default", "api_key": key1})
            self.check("S6 创建租户 Key", r.status_code == 200, f"HTTP {r.status_code}")

            self.check("S6 无 Key → 401", self.c.get("/api/sessions", headers=no_h).status_code == 401)
            self.check("S6 错 Key → 401", self.c.get("/api/sessions", headers={"X-API-Key": "wrong-key-12345678"}).status_code == 401)
            ok = self.c.get("/api/sessions", headers={"X-API-Key": key1, "X-Tenant-ID": "default"})
            self.check("S6 租户 Key 放行", ok.status_code == 200, f"HTTP {ok.status_code}")
            self.check("S6 租户 Key 管理面 403",
                       self.c.get("/api/admin/status", headers={"X-API-Key": key1}).status_code == 403)

            r = self.c.post("/api/settings/tenant-keys", headers=admin_h, json={"tenant_id": "default", "api_key": key2})
            self.check("S6 轮换 Key（admin）", r.status_code == 200, f"HTTP {r.status_code}")
            self.check("S6 旧 Key 立即失效", self.c.get("/api/sessions", headers={"X-API-Key": key1}).status_code == 401)
            self.check("S6 新 Key 生效", self.c.get("/api/sessions", headers={"X-API-Key": key2}).status_code == 200)

            r = self.c.post("/api/settings/tenant-keys", headers=admin_h, json={"tenant_id": "default", "api_key": ""})
            self.check("S6 删除 Key（admin）", r.status_code == 200, f"HTTP {r.status_code}")
            self.check("S6 删除后租户 Key 失效", self.c.get("/api/sessions", headers={"X-API-Key": key2}).status_code == 401)
            self.check("S6 admin Key 始终有效", self.c.get("/api/sessions", headers=admin_h).status_code == 200)
        finally:
            # 还原租户 Key 文件（不污染真实配置）
            if file_existed:
                self.tenant_keys_file.write_text(orig_content, encoding="utf-8")
            elif self.tenant_keys_file.exists():
                try:
                    self.tenant_keys_file.unlink()
                except OSError:
                    pass

    # ── S7 前端冒烟 ──

    def s7_frontend(self):
        r = httpx.get(f"{self.frontend}/", timeout=60)
        self.check("S7 首页 200", r.status_code == 200, f"HTTP {r.status_code}")
        self.check("S7 React root 挂载", 'id="root"' in r.text)
        r2 = httpx.get(f"{self.frontend}/health", timeout=30)
        self.check("S7 前端代理 /health", r2.status_code == 200 and "healthy" in r2.text, f"HTTP {r2.status_code}")

    # ── S8 风格词库（A79-A96：用户指定的「风格词库」页面链路）──

    def s8_style_library(self):
        """建词条 → 分析（Mock：$0）→ 列表/详情 → 预览 → 停用 → 删除

        注意：Mock 模式下「风格档案员」返回**模板词条**（$0，不联网），所以这条场景零成本。
        """
        listing = self.c.get("/api/style-library")
        self.check("S8 词库列表可用", listing.status_code == 200, f"HTTP {listing.status_code}")
        data = listing.json()
        self.check("S8 内置档案齐备", len(data.get("builtin") or []) >= 8,
                   f"builtin={len(data.get('builtin') or [])}")
        self.check("S8 统计含开关与上限",
                   "enabled" in (data.get("stats") or {}) and "max_entries" in (data["stats"]),
                   f"stats={ {k: data.get('stats', {}).get(k) for k in ('enabled', 'max_entries')} }")

        preview = self.c.get("/api/style-library/preview?platform=taobao&slot=main_white")
        self.check("S8 零成本预览", preview.status_code == 200 and "适用风格档案" in preview.json()["block"],
                   f"HTTP {preview.status_code} chars={len(preview.json().get('block') or '')}")

        created = self.c.post("/api/style-library",
                              data={"name": "E2E 冒烟风格"},
                              files=[("files", ("style.jpg", make_test_image(), "image/jpeg"))])
        self.check("S8 建词条", created.status_code == 200, f"HTTP {created.status_code}")
        if created.status_code != 200:
            return
        entry = created.json()["entry"]
        eid = entry["id"]
        self.check("S8 用量前置（金额未标定不编数）",
                   created.json()["estimate"]["amount"] is None
                   and created.json()["estimate"]["images"] == 1,
                   f"estimate={created.json().get('estimate')}")
        try:
            detail = self.poll(lambda: self.c.get(f"/api/style-library/{eid}").json()["entry"],
                               lambda item: item["status"] in ("ready", "failed"),
                               "风格分析完成")
            self.check("S8 分析完成（Mock 为模板词条）", detail["status"] == "ready",
                       f"status={detail['status']} error={detail.get('error')}")
            self.check("S8 逐字段可编辑所需字段齐全",
                       bool(detail.get("background")) and bool(detail.get("taste_verdict")),
                       f"background={bool(detail.get('background'))} verdict={bool(detail.get('taste_verdict'))}")
            self.check("S8 事实中立（无商标符号/色值）",
                       "®" not in json.dumps(detail, ensure_ascii=False)
                       and not re.search(r"#[0-9A-Fa-f]{6}", json.dumps(detail, ensure_ascii=False)),
                       "词条文本应不含 ® 或 #RRGGBB")
            edited = self.c.patch(f"/api/style-library/{eid}",
                                  json={"name": "E2E 冒烟风格（改名）", "as_anchor": False})
            self.check("S8 编辑词条", edited.status_code == 200
                       and edited.json()["entry"]["name"].endswith("（改名）"),
                       f"HTTP {edited.status_code}")
            stopped = self.c.patch(f"/api/style-library/{eid}", json={"enabled": False})
            self.check("S8 停用词条", stopped.status_code == 200
                       and stopped.json()["entry"]["enabled"] is False,
                       f"HTTP {stopped.status_code}")
        finally:
            removed = self.c.delete(f"/api/style-library/{eid}")
            self.check("S8 删除词条", removed.status_code == 200, f"HTTP {removed.status_code}")
        gone = self.c.get(f"/api/style-library/{eid}")
        self.check("S8 删除后不可见", gone.status_code == 404, f"HTTP {gone.status_code}")

    # ── 调度 ──

    SCENARIOS = {
        "S1": s1_group_chat, "S2": s2_workflow, "S3": s3_batch,
        "S4": s4_memory_audit, "S5": s5_settings, "S6": s6_auth, "S7": s7_frontend,
        "S8": s8_style_library,
    }

    def run(self, selected: list[str]):
        print("\n=== E2E 冒烟开始 ===")
        for name in selected:
            try:
                self.SCENARIOS[name](self)
                self.results.append((name, True, ""))
            except Exception as e:  # noqa: BLE001 — 冒烟脚本收集全部失败
                self.results.append((name, False, str(e)[:200]))
                print(f"  {FAIL} {name} — {str(e)[:200]}")
        print("\n=== 汇总 ===")
        passed = sum(1 for _, ok, _ in self.results if ok is True)
        skipped = sum(1 for _, ok, _ in self.results if ok is None)
        for name, ok, detail in self.results:
            if ok is None:
                print(f"  ⏭ SKIP {name}")
            else:
                print(f"  {PASS if ok else FAIL} {name}" + (f" — {detail}" if detail else ""))
        print(f"  通过 {passed}/{len(self.results) - skipped}" + (f"（跳过 {skipped}）" if skipped else ""))
        return passed == len(self.results) - skipped


def ensure_mock(e2e: E2E) -> bool:
    """严格 Mock 判定：mock_mode=true 且无任何已配置的 Provider Key

    注意：仅 MOCK_MODE=true 不够——secrets.yaml 注入的 Key 会让 Agent 走真实 API
    （Coordinator/视觉调用真实 DeepSeek，行为不确定且会卡 SVG 占位图）。
    """
    try:
        h = e2e.c.get("/health").json()
        if h.get("mock_mode") is not True:
            return False
        s = e2e.c.get("/api/settings").json()
        configured = [k["env"] for k in s.get("api_keys", []) if k.get("configured")]
        return len(configured) == 0
    except Exception:
        return False


def _stop_children(children: list, ports: list[int] | None = None) -> None:
    """停止**本次冒烟自己启动**的子进程。Windows 下 npm→cmd→node 是多级孙进程，
    Popen 的 npm 包装退出后后代会被重挂载——用 ctypes 按端口查监听 PID 直接
    TerminateProcess（进程内 API，不依赖 netstat/taskkill 等外部命令，受限环境也可靠）。

    第三轮审计 B2-17：`ports` 只传「本次确实由我们启动」的端口。此前无条件
    `_kill_port_listeners([8000, 5173])`，`--existing`（复用已运行服务）也会把用户
    自己的前后端杀掉；自动模式下端口若本就属于别的进程，同样会被误杀。
    """
    for p in children:
        if p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    for p in children:
        try:
            p.wait(timeout=4)
        except Exception:
            pass
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
    if os.name == "nt" and ports:
        _kill_port_listeners(list(ports))


def _port_in_use(port: int) -> bool:
    """端口是否已有监听者（IPv4 + IPv6 双探：vite 默认绑 ::1，只探 127.0.0.1 会漏）"""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.4)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def _url_port(url: str, default: int) -> int:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return default
    return parsed.port or {"http": 80, "https": 443}.get(parsed.scheme, default)


def _auto_mode_conflict(base_url: str, frontend_url: str, probe=_port_in_use) -> str:
    """自动启动模式的前置校验：返回拒绝原因（空字符串 = 可以启动）。

    第三轮审计 B2-17，两个静默陷阱：
    1. 端口被占用时照常启动 → `backend_ready()` 的健康检查命中**别人**的服务，
       脚本以为"就绪"，实际在验证另一个实例（可能配了真实 Key），收尾还会杀掉它；
    2. `--base-url/--frontend-url` 在自动模式下被忽略 → 给自定义端口也照样起 8000/5173，
       而 vite 代理硬编码 localhost:8000（`frontend/vite.config.js`）→ 前后端错配。
    """
    backend_port = _url_port(base_url, _DEFAULT_BACKEND_PORT)
    frontend_port = _url_port(frontend_url, _DEFAULT_FRONTEND_PORT)
    if backend_port != _DEFAULT_BACKEND_PORT or frontend_port != _DEFAULT_FRONTEND_PORT:
        return (f"自动启动模式只支持默认端口（后端 {_DEFAULT_BACKEND_PORT} / 前端 {_DEFAULT_FRONTEND_PORT}），"
                f"当前指定后端 {backend_port} / 前端 {frontend_port}。"
                f"自定义端口请先自行启动服务，再用 --existing 复用。")
    for label, port in (("后端", backend_port), ("前端", frontend_port)):
        if probe(port):
            return (f"自动启动模式要求端口空闲，但{label}端口 {port} 已被占用"
                    f"（可能正是你正在使用的服务）。请先停止它，或改用 --existing 复用。")
    return ""


def _kill_port_listeners(ports: list[int]) -> None:
    """Windows：按端口找监听进程并强杀（ctypes 直调 Win32，无外部命令依赖）"""
    pids = _win_tcp_listener_pids(set(ports))
    _win_terminate_pids(pids)


def _win_tcp_listener_pids(ports: set[int]) -> set[int]:
    """GetExtendedTcpTable：返回指定端口上 LISTENING 的进程 PID 集合（IPv4 + IPv6 双表）

    注意：vite 默认绑定 localhost（IPv6 ::1），只查 AF_INET 会漏掉它。
    IPv6 行的结构体字段顺序与 IPv4 不同（dwState 在末尾），但按名字访问不受影响。
    """
    import ctypes
    from ctypes import wintypes

    class _ROW4(ctypes.Structure):
        _fields_ = [
            ("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD), ("dwRemoteAddr", wintypes.DWORD),
            ("dwRemotePort", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD),
        ]

    class _ROW6(ctypes.Structure):
        # 注意：IPv6 行的 dwState 在 dwOwningPid 之前（与 IPv4 行相反），顺序不可调换
        _fields_ = [
            ("ucLocalAddr", ctypes.c_ubyte * 16), ("dwLocalScopeId", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD), ("ucRemoteAddr", ctypes.c_ubyte * 16),
            ("dwRemoteScopeId", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD),
            ("dwState", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD),
        ]

    AF_INET, AF_INET6, TCP_TABLE_OWNER_PID_ALL, MIB_TCP_STATE_LISTEN = 2, 23, 5, 2
    get_table = ctypes.windll.iphlpapi.GetExtendedTcpTable
    get_table.restype = wintypes.DWORD

    pids = set()
    for af, row_type in ((AF_INET, _ROW4), (AF_INET6, _ROW6)):
        size = wintypes.DWORD(0)
        get_table(None, ctypes.byref(size), False, af, TCP_TABLE_OWNER_PID_ALL, 0)
        buf = ctypes.create_string_buffer(size.value)
        if get_table(buf, ctypes.byref(size), False, af, TCP_TABLE_OWNER_PID_ALL, 0) != 0:
            continue
        count = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong)).contents.value
        rows = ctypes.cast(ctypes.addressof(buf) + 4, ctypes.POINTER(row_type))
        for i in range(count):
            row = rows[i]
            if row.dwState == MIB_TCP_STATE_LISTEN and socket.ntohs(row.dwLocalPort) in ports:
                pids.add(int(row.dwOwningPid))
    return pids


def _win_terminate_pids(pids: set[int]) -> None:
    """OpenProcess + TerminateProcess 强杀（进程内，无外部命令依赖）"""
    if not pids:
        return
    import ctypes
    from ctypes import wintypes

    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    for pid in pids:
        try:
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="E2E 冒烟（测试计划 P2）")
    parser.add_argument("--existing", action="store_true", help="复用已在运行的服务（默认自动启动）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--frontend-url", default="http://localhost:5173")
    parser.add_argument("--scenarios", default="S1,S2,S3,S4,S5,S6,S7,S8", help="逗号分隔的场景列表")
    parser.add_argument("--timeout", type=int, default=120, help="单场景等待超时（秒）")
    parser.add_argument("--real", action="store_true", help="允许真实模式（不要求 Mock，断言可能不稳）")
    parser.add_argument("--admin-key", default="", help="管理 Key（默认读 ECOMM_API_KEY；S6 需要它执行租户 Key 轮换/删除）")
    args = parser.parse_args()

    admin_key = args.admin_key or os.getenv(ADMIN_KEY_ENV, "")
    e2e = E2E(args.base_url, args.frontend_url, args.timeout, admin_key=admin_key)
    started: list = []
    owned_ports: list[int] = []   # 第三轮审计 B2-17：只清理本次真正启动的端口

    try:
        if not args.existing:
            import start as launcher  # noqa: PLC0415

            # 第三轮审计 B2-17：先预检端口（被占用/自定义端口 → 明确拒绝，
            # 不"认领"别人的服务，也不把别人的实例当成就绪）
            conflict = _auto_mode_conflict(args.base_url, args.frontend_url)
            if conflict:
                print(f"❌ {conflict}")
                return 2

            print("🚀 自动启动服务（确定性 Mock：清空 Provider Key + MOCK_MODE=true + 注入管理 Key）")
            # 备份并清空 Provider Key（防 secrets.yaml 注入真实 Key → Agent 走真实 API）
            saved_env = {k: os.environ.get(k) for k in _provider_key_envs()}
            for k in _provider_key_envs():
                os.environ[k] = ""
            os.environ["MOCK_MODE"] = "true"
            os.environ.setdefault(ADMIN_KEY_ENV, "e2e-admin-key-12345678")
            admin_key = admin_key or os.environ[ADMIN_KEY_ENV]
            e2e.admin_key = admin_key
            e2e.c.headers.update({"X-API-Key": admin_key})

            e2e.c.close()
            backend = launcher.start_backend(_DEFAULT_BACKEND_PORT)
            started.append(backend)
            owned_ports.append(_DEFAULT_BACKEND_PORT)
            # 子进程已继承环境，恢复父进程环境
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            if not launcher.wait_until(lambda: launcher.backend_ready(_DEFAULT_BACKEND_PORT), "后端", timeout_s=args.timeout):
                return 2
            e2e.c = httpx.Client(base_url=args.base_url, timeout=90, headers={"X-API-Key": admin_key})
            frontend = launcher.start_frontend(_DEFAULT_FRONTEND_PORT)
            started.append(frontend)
            owned_ports.append(_DEFAULT_FRONTEND_PORT)
            if not launcher.wait_until(lambda: launcher.frontend_ready(_DEFAULT_FRONTEND_PORT), "前端", timeout_s=args.timeout):
                return 2

        if not ensure_mock(e2e) and not args.real:
            print("❌ 非确定性 Mock：mock_mode 非 true 或存在已配置的 Provider Key（secrets.yaml 注入会让 Agent 走真实 API）。"
                  "自动启动模式会自动清空 Key；--existing 请使用无 Key 的后端，或加 --real。")
            return 2

        selected = [s.strip().upper() for s in args.scenarios.split(",") if s.strip()]
        unknown = [s for s in selected if s not in E2E.SCENARIOS]
        if unknown:
            print(f"❌ 未知场景: {unknown}")
            return 2

        ok = e2e.run(selected)
        print(f"\n{'🎉 E2E 冒烟全部通过' if ok else '💥 E2E 冒烟存在失败'}")
        return 0 if ok else 1
    finally:
        e2e.cleanup_sessions()
        e2e.c.close()
        # 只清理本次启动的进程/端口（--existing 复用模式下 owned_ports 为空 → 不碰用户的服务）
        _stop_children(started, owned_ports)


if __name__ == "__main__":
    sys.exit(main())
