"""生成图产物**按槽位合并**（A74）

改前 `_update_artifacts` 对 `artifacts["images"]` 是整键替换：只要有一轮只出了部分槽位
（补跑详情图、只重出某几张、单槽位子集冒烟），上一轮已出的图就会从产物里消失
（磁盘文件还在，界面与导出却没了）。
"""

from src.chat.engine import _merge_images


def _img(slot: str, url: str = "") -> dict:
    return {"slot_id": slot, "prompt_name": slot, "image_url": url or f"https://cdn/{slot}.jpg"}


class TestMergeImages:
    def test_same_slot_is_replaced_in_place(self):
        merged = _merge_images([_img("a", "old-a"), _img("b")], [_img("a", "new-a")])
        assert [item["slot_id"] for item in merged] == ["a", "b"]
        assert merged[0]["image_url"] == "new-a"      # 原位替换，顺序不变
        assert merged[1]["image_url"] == "https://cdn/b.jpg"   # 其他槽位保留

    def test_new_slot_is_appended(self):
        merged = _merge_images([_img("a")], [_img("c")])
        assert [item["slot_id"] for item in merged] == ["a", "c"]

    def test_no_usable_key_falls_back_to_replace(self):
        """旧路径（单图多候选）没有 slot_id/prompt_name 之外的键 → 整批替换"""
        old = [{"prompt_name": "", "image_url": "old"}]
        new = [{"prompt_name": "", "image_url": "new"}]
        assert _merge_images(old, new) == new

    def test_empty_inputs_are_safe(self):
        assert _merge_images(None, []) == []
        assert _merge_images([_img("a")], None) == [_img("a")]
        assert _merge_images(None, [_img("a")]) == [_img("a")]

    def test_seeded_from_existing_when_no_prior_images(self):
        assert _merge_images([], [_img("a"), _img("b")]) == [_img("a"), _img("b")]
