"""审美锚点脚本（`scripts/style_anchor.py`）—— 照片 → 文字判词

用户 2026-09-18 指定「风格词库」时要求"导入照片 → 命名 → 让专门的 agent 分析这组照片的风格"。
界面版在「风格词库」页；这个脚本是无界面/自动化版，并且**默认不写文件**（先看判词再决定）。

测试盯住：
1. `--print` 零成本且产出**合法 YAML**（能被加载链路解析成锚点）；
2. 判词经 `sanitize_entry` 清洗（品牌/成分/认证/色值不进锚点）；
3. `--append` 只改 `anchors:` 段、**先备份**，且不会破坏文件其它内容（注释就是文档）；
4. `--dry-run` **不调用模型**（不花钱）。
"""

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location("style_anchor", ROOT / "scripts" / "style_anchor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["style_anchor"] = module
    spec.loader.exec_module(module)
    return module


anchor_script = _load_script()


def test_template_is_valid_yaml():
    payload = yaml.safe_load(anchor_script.template())
    assert isinstance(payload.get("anchors"), list) and payload["anchors"]
    anchor = payload["anchors"][0]
    assert anchor["name"] and anchor["taste_verdict"]


def test_rendered_anchor_parses_and_normalizes():
    from src.harness.style_library import normalize_anchor

    block = anchor_script.render_anchor_yaml(
        {"taste_verdict": "冷白留白、投影极淡", "reward_points": ["留白充足"],
         "avoid_points": ["暖黄调"], "style_words": "x"},
        "冷白实验室感", note="用户锚点（测试）")
    payload = yaml.safe_load(block)
    assert payload["anchors"][0]["id"] == "anchor_冷白实验室感".lower() or \
        payload["anchors"][0]["id"].startswith("anchor_")
    anchor = normalize_anchor(payload["anchors"][0])
    assert anchor and anchor["name"] == "冷白实验室感"
    assert anchor["taste_verdict"] == "冷白留白、投影极淡"


def test_brand_text_is_stripped_before_rendering():
    from src.harness.style_library import sanitize_entry

    cleaned, removed = sanitize_entry({
        "taste_verdict": "冷白留白；品牌某®风格；含 12 种成分",
        "reward_points": ["留白充足", "通过 GMP 认证"],
    })
    assert "成分" not in cleaned["taste_verdict"]
    assert removed
    block = anchor_script.render_anchor_yaml(cleaned, "测试锚点")
    assert "GMP" not in block and "®" not in block


def test_append_replaces_empty_placeholder(tmp_path, monkeypatch):
    target = tmp_path / "style_library.yaml"
    target.write_text("# 注释是文档，不许丢\nentries: []\nanchors: []\n", encoding="utf-8")
    monkeypatch.setattr(anchor_script, "ROOT", tmp_path)
    monkeypatch.setattr(anchor_script, "STYLE_LIBRARY_REL", "style_library.yaml")

    block = anchor_script.render_anchor_yaml({"taste_verdict": "冷白留白"}, "我的锚点")
    anchor_script.append_to_library(block)

    text = target.read_text(encoding="utf-8")
    assert "# 注释是文档，不许丢" in text, "注释不能被程序吃掉"
    payload = yaml.safe_load(text)
    assert payload["anchors"][0]["name"] == "我的锚点"
    assert (tmp_path / "style_library.yaml.bak").exists(), "要先备份"


def test_append_adds_to_existing_anchors(tmp_path, monkeypatch):
    target = tmp_path / "style_library.yaml"
    target.write_text(
        "entries: []\nanchors:\n  - id: a1\n    name: 老锚点\n    taste_verdict: 旧的\n",
        encoding="utf-8")
    monkeypatch.setattr(anchor_script, "ROOT", tmp_path)
    monkeypatch.setattr(anchor_script, "STYLE_LIBRARY_REL", "style_library.yaml")

    anchor_script.append_to_library(
        anchor_script.render_anchor_yaml({"taste_verdict": "新的"}, "新锚点"))
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    names = [item["name"] for item in payload["anchors"]]
    assert names == ["老锚点", "新锚点"], names


def test_append_creates_section_when_missing(tmp_path, monkeypatch):
    target = tmp_path / "style_library.yaml"
    target.write_text("entries: []\n", encoding="utf-8")
    monkeypatch.setattr(anchor_script, "ROOT", tmp_path)
    monkeypatch.setattr(anchor_script, "STYLE_LIBRARY_REL", "style_library.yaml")

    anchor_script.append_to_library(
        anchor_script.render_anchor_yaml({"taste_verdict": "新的"}, "新锚点"))
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert payload["anchors"][0]["name"] == "新锚点"


def test_dry_run_does_not_call_models(monkeypatch, capsys):
    async def _boom(*args, **kwargs):
        raise AssertionError("dry-run 不允许调用模型（会花钱）")

    monkeypatch.setattr(anchor_script, "analyze", _boom)
    monkeypatch.setattr(sys, "argv",
                        ["style_anchor.py", "--images", "config/platforms.yaml",
                         "--name", "测试", "--dry-run"])
    assert anchor_script.main() == 0
    assert "未花钱" in capsys.readouterr().out


def test_print_needs_no_images(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["style_anchor.py", "--print"])
    assert anchor_script.main() == 0
    assert "anchors:" in capsys.readouterr().out


def test_no_images_falls_back_to_template(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["style_anchor.py", "--images"])
    assert anchor_script.main() == 0
    out = capsys.readouterr().out
    assert "anchors:" in out and "零成本模板" in out


def test_missing_image_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["style_anchor.py", "--images", "nope.jpg", "--name", "x"])
    with pytest.raises(SystemExit):
        anchor_script.main()


def test_name_required(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["style_anchor.py", "--images", "config/platforms.yaml"])
    with pytest.raises(SystemExit):
        anchor_script.main()
