"""白未晞生成质量回归：池条目必须携带针对性生成指导。"""
from __future__ import annotations

from pathlib import Path

import yaml

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory


ROOT = Path(__file__).resolve().parents[2]
POOLS = ROOT / "profiles" / "baiweixi" / "pools.yaml"
MERGE_PROMPT = ROOT / "data_gen_v4" / "prompts" / "reply_merge.txt"

TARGET_TOPICS = {
    "周末一起去超市",
    "学打招呼",
    "第一次吃冰淇淋",
    "她隔着纸箱看你",
    "她做噩梦",
    "生活常识",
    "公交刷卡",
    "医院挂号机",
}


def _pool_entries() -> dict[str, dict]:
    doc = yaml.safe_load(POOLS.read_text(encoding="utf-8"))
    return {
        entry["topic"]: entry
        for entries in (doc.get("pools") or {}).values()
        for entry in entries
        if entry.get("topic") in TARGET_TOPICS
    }


def test_known_bad_topics_have_explicit_generation_guidance():
    entries = _pool_entries()
    assert set(entries) == TARGET_TOPICS
    for topic, entry in entries.items():
        guidance = entry.get("generation_guidance", "")
        assert guidance, f"{topic} 缺少针对性生成指导"
        assert len(guidance) >= 20, f"{topic} 的指导过于粗糙"


def test_generation_guidance_survives_plan_input():
    entries = _pool_entries()
    factory = RecipeDrivenItemFactory(pools_path=str(POOLS))
    for index, (topic, entry) in enumerate(entries.items()):
        item = factory._item_from_sample(
            {**entry, "task_type": "reply_item", "mode_id": "REPLY"},
            seed=42,
            index=index,
        )
        assert item.input["generation_guidance"] == entry["generation_guidance"], topic


def test_reply_prompt_has_quality_and_role_boundaries():
    prompt = MERGE_PROMPT.read_text(encoding="utf-8")
    assert "{{GENERATION_GUIDANCE}}" in prompt
    assert "动作与对白分开" in prompt
    assert "玩家负责解释" in prompt
    assert "不要装懂" in prompt
