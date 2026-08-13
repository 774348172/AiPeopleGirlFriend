"""白未晞深层人物合同：设定、锚和生成规则必须同一口径。"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from data_gen_v4.adapters.modes.renderers import ProductionRenderers
from gen_v4 import PROFILES_ROOT, ROOT, build_package_set
from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.adapters.sources.registry import CompositeSourceLoader, FilePackageRegistry
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.plan import RunSpec


def test_baiweixi_anchor_carries_cohabitation_and_behavior_contract():
    package_set = build_package_set(PROFILES_ROOT, "baiweixi")
    result = GenerationPlanCompiler(
        FilePackageRegistry(PROFILES_ROOT),
        CompositeSourceLoader(ROOT),
        RecipeDrivenItemFactory(pools_path=str(PROFILES_ROOT / "baiweixi" / "pools.yaml")),
    ).compile(RunSpec(run_id="deep-contract", seed=42), package_set)
    profile = result.context["profile"]
    anchor = ProductionRenderers.reply_system_anchor(
        profile,
        anchor_facts=result.context.get("anchor_facts") or {},
        beliefs=str(result.context.get("beliefs", "")),
        rules=result.context["protocol"].get("reply_runtime_rules") or [],
    )
    assert "共同生活" in anchor
    assert "看见、听见" in anchor or "场景内" in anchor
    assert "伤好会走" in anchor or "不想离开" in anchor
    assert "现代知识" in anchor or "手机" in anchor


def test_baiweixi_known_past_is_not_marked_as_player_secret():
    profile = yaml.safe_load((ROOT / "profiles/baiweixi/profile.yaml").read_text(encoding="utf-8"))
    policy = profile["policy_terms"]
    forbidden = set(policy.get("forbidden_human_text") or [])
    assert "深山" not in forbidden
    assert "化形" not in forbidden
    assert "妖果" not in forbidden
    prompt = profile["prompt_policy_block"]
    assert "不知道妖果" not in prompt
    assert "不知道深山" not in prompt


def test_baiweixi_name_known_and_origin_unknown_are_separate_facts():
    canon = json.loads((ROOT / "人物设定/白未晞/canon.json").read_text(encoding="utf-8"))
    facts = canon["facts"]
    assert facts["name_known_since_awakening"]["disclosure_policy"] == "direct_allowed"
    assert facts["name_origin_status"]["disclosure_policy"] == "direct_allowed"
    assert "随妖族传承一起觉醒" not in facts["name_origin_status"]["value"]
