"""协调者必须看见「商品身份 / 套图覆盖度 / 本地体检」这三件事

用户三点要求的闭环：协调者是决定"下一步做什么"的人，如果它看不到
① 品牌有没有确认、② 套图缺哪几张、③ 体检报了什么，它就会宣布"任务完成"
—— 实测正是如此（产出 3 张同一提示词的候选，还被判成完成）。
"""

from src.agents.coordinator import CoordinatorAgent, readable_content


def _session():
    return {"session_id": "s1", "tenant_id": "default", "turn_count": 3,
            "messages": [], "error_history": [],
            "task": {"platform": "taobao", "product_info": "测试", "category_hint": "保健品"},
            "artifacts": {}}


class TestArtifactStatus:
    def test_identity_confirmed_line(self):
        session = _session()
        session["artifacts"]["analysis"] = {"category": "保健品"}
        session["artifacts"]["product_identity"] = {
            "status": "confirmed", "brand": "DEFOEBUENA®", "product_name": "金裝強力肝迅康",
            "spec": "60's", "source": "vision", "missing": []}
        text = CoordinatorAgent(provider=None)._artifact_status(session)
        assert "商品身份：**已确认**" in text
        assert "DEFOEBUENA®" in text and "金裝強力肝迅康" in text

    def test_identity_unconfirmed_line_forbids_text(self):
        session = _session()
        session["artifacts"]["product_identity"] = {
            "status": "uncertain", "brand": "", "product_name": "", "source": "vision",
            "missing": ["品牌", "商品名"]}
        text = CoordinatorAgent(provider=None)._artifact_status(session)
        assert "未确认" in text
        assert "不得" in text and "品牌" in text

    def test_set_plan_and_coverage_lines(self):
        session = _session()
        session["artifacts"]["prompts"] = {"set_plan": {"platform": "taobao"}}
        session["artifacts"]["set_plan"] = {"platform": "taobao", "platform_label": "淘宝",
                                            "slots": [{"slot_id": "main_white"},
                                                      {"slot_id": "main_scene"}]}
        session["artifacts"]["images"] = [
            {"slot_id": "main_white", "image_url": "https://x/1", "prompt_name": "main_white"}]
        session["artifacts"]["set_plan_coverage"] = {
            "expected": 2, "produced": 1, "missing_slots": ["main_scene"], "complete": False}
        text = CoordinatorAgent(provider=None)._artifact_status(session)
        assert "套图编排" in text and "main_white" in text
        assert "套图不完整" in text and "main_scene" in text
        assert "[main_white]" in text, "逐图要带槽位，协调者才知道缺哪张"

    def test_missing_set_plan_is_flagged(self):
        session = _session()
        session["artifacts"]["prompts"] = {"main_image": {"prompt": "旧单图"}}
        session["artifacts"]["images"] = [{"prompt_name": "variant_1", "image_url": "https://x/1"}]
        text = CoordinatorAgent(provider=None)._artifact_status(session)
        assert "无套图编排" in text
        assert "多个候选" in text

    def test_quality_report_lines(self):
        session = _session()
        session["artifacts"]["images"] = [{"slot_id": "main_white", "image_url": "https://x/1"}]
        session["artifacts"]["quality_report"] = {
            "count": 1, "white_bg_ok": False, "watermark_free": True,
            "identity_lost": ["main_white"], "near_copy": [],
            "issues": ["main_white：背景不是纯白：边缘平均亮度 238 < 250"]}
        text = CoordinatorAgent(provider=None)._artifact_status(session)
        assert "本地体检" in text and "白底合格=否" in text
        assert "身份相似度过低" in text
        assert "背景不是纯白" in text

    def test_empty_artifacts(self):
        assert "尚无产物" in CoordinatorAgent(provider=None)._artifact_status(_session())


class TestSystemPrompt:
    def test_prompt_defines_set_deliverable_and_identity_rule(self):
        prompt = CoordinatorAgent(provider=None)._fallback_system_prompt("- 商品分析员")
        assert "一整套可上传的图" in prompt
        assert "套图不完整" in prompt
        assert "商品身份未确认" in prompt
        assert "本地体检" in prompt

    def test_prompt_forbids_reinviting_ready_stages(self):
        """状态里已就绪的产出物不许重复邀请（实录：品类专项分析员连跑两次）"""
        prompt = CoordinatorAgent(provider=None)._fallback_system_prompt("- 商品分析员")
        assert "不要再邀请对应 Agent" in prompt
        assert "重复邀请" in prompt
        assert "⛔" in prompt


class TestReadableContent:
    """群聊历史必须给协调者**人话**，不是 Python 字典原文

    用户反馈（2026-09-20）："会话里 agent 的对话里面显示的会很直白，会给出代码原文"。
    协调者侧的同一问题更贵：`str(content)[:400]` 每轮重复进上下文（对账：某会话审计只记
    $0.010999、实际 $0.018143，差额全在协调者每轮的 decide 调用），且 10 条里 5 条被截断。
    """

    def test_message_wins_over_structure(self):
        text = readable_content({
            "quality_report": {"count": 10},
            "set_plan_coverage": {"expected": 10, "produced": 8},
            "message": "图一（真实商品图）+ 生成图的双条件出图完成，共 10 张",
        })
        assert "双条件出图完成" in text
        assert "{" not in text

    def test_error_and_invite_shapes(self):
        assert "❌" in readable_content({"error": "生图员 超时 (420000ms)"})
        invite = readable_content({"action": "invite", "agent_name": "生图员",
                                   "task_brief": "生成拼多多套图"})
        assert "邀请 生图员" in invite and "生成拼多多套图" in invite

    def test_image_and_quality_shapes_are_readable(self):
        text = readable_content({
            "images": [{"slot_id": "main_white", "image_url": "https://x/a.jpg"},
                       {"slot_id": "main_spec", "error": "超时"}],
            "image_notes": ["已启用文+图双条件：4 张真实商品图作为参考图"],
        })
        assert "出图 2 张" in text and "失败 1 张" in text
        assert "main_spec" in text and "{" not in text

        quality = readable_content({
            "quality_report": {"count": 10, "white_bg_ok": True,
                               "identity_lost": ["main_white", "main_spec"]},
            "set_plan_coverage": {"expected": 10, "produced": 8,
                                  "blocked_slots": ["main_usage", "main_compare"]},
            "message": "出图完成，共 10 张",
        })
        assert "白底合格=是" in quality and "套图 8/10" in quality and "缺素材 2 张" in quality

    def test_unknown_shape_falls_back_to_key_names_not_structure(self):
        text = readable_content({"zzz": 1, "yyy": {"deep": [1, 2, 3]}})
        assert text.startswith("字段：")
        assert "{" not in text and "[" not in text

    def test_truncation_is_reported_not_silent(self):
        long = "很长的一句商品事实。" * 100
        text = readable_content({"message": long})
        assert "（原文共" in text
        assert len(text) < len(long)


class TestUserPromptCarriesReadableHistory:
    def test_no_dict_repr_in_history(self):
        session = _session()
        session["messages"] = [
            {"sender": "生图员", "content": {"images": [
                {"slot_id": "main_white", "prompt_text": "很长的提示词" * 200}],
                "cost_unknown": True}},
            {"sender": "系统", "content": {"quality_report": {"count": 10},
                                            "message": "本地体检发现 2 个问题"}},
        ]
        prompt = CoordinatorAgent(provider=None)._build_user_prompt(session, "")
        assert "{'images'" not in prompt          # 旧实现就是这个
        assert "本地体检发现 2 个问题" in prompt
        assert "完整状态以上方" in prompt

