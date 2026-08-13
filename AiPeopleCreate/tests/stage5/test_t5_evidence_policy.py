"""T5：evidence_state / desired_policy 落地（2026-08-06）。

覆盖：
- factory 按 recipe 分布确定性采样（insufficient/false_premise 有量、同 seed 可复现）；
- 生成 prompt 渲染【证据状态】【目标策略】指令（insufficient → 保持未知；false_premise → 不顺着错误前提；withhold → 不透露秘密）。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory  # noqa: E402
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter  # noqa: E402
from data_gen_v4.adapters.sources.registry import (  # noqa: E402
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import RunSpec  # noqa: E402
from gen_qin_v4 import PROFILES_ROOT, QWX_PACKAGE_SET, ROOT as GEN_ROOT  # noqa: E402
from tests.adapters.conftest import REPLY_OK_SCRIPT  # noqa: E402
from tests.adapters.test_reply_adapter import _run  # noqa: E402


@pytest.fixture(scope="module")
def qin_plan():
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    factory = RecipeDrivenItemFactory(pools_path=str(PROFILES_ROOT / "qinweixi" / "pools.yaml"))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    return compiler.compile(RunSpec(run_id="t5-check", seed=42), QWX_PACKAGE_SET).plan


def test_evidence_distribution_sampled_per_recipe(qin_plan):
    by_task = {}
    for item in qin_plan.items:
        by_task.setdefault(item.task_type, []).append(item.evidence_state)
    casual = Counter(by_task["reply_casual"])
    n = len(by_task["reply_casual"])
    # recipe: casual supported 0.85 / insufficient 0.10 / false_premise 0.05
    assert abs(casual["insufficient"] / n - 0.10) < 0.03
    assert abs(casual["false_premise"] / n - 0.05) < 0.03
    # 总量门槛：insufficient ≥5%、false_premise ≥2%（T5 验收）
    all_states = [i.evidence_state for i in qin_plan.items]
    total = len(all_states)
    assert Counter(all_states)["insufficient"] / total >= 0.05
    assert Counter(all_states)["false_premise"] / total >= 0.02


def test_sampling_is_deterministic_for_same_seed(qin_plan):
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    factory = RecipeDrivenItemFactory(pools_path=str(PROFILES_ROOT / "qinweixi" / "pools.yaml"))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    plan2 = compiler.compile(RunSpec(run_id="t5-check-again", seed=42), QWX_PACKAGE_SET).plan
    assert [i.evidence_state for i in qin_plan.items] == [i.evidence_state for i in plan2.items]
    assert [i.desired_policy for i in qin_plan.items] == [i.desired_policy for i in plan2.items]


def test_pool_entry_explicit_evidence_state_wins_over_distribution(qin_plan):
    # pool 里显式声明的 evidence_state 优先于 recipe 分布采样
    prot = {i.family_id: i for i in qin_plan.items if i.task_type == "reply_protective"}
    # prot-01 在 pools.yaml 显式声明 evidence_state: supported（即使 protective 分布含 false_premise 0.2）
    assert prot["prot-01"].evidence_state == "supported"
    # prot-07 显式声明 not_required
    assert prot["prot-07"].evidence_state == "not_required"


def test_insufficient_prompt_asks_to_stay_unknown(reply_item, package_context):
    item = dict(reply_item, evidence_state="insufficient", desired_policy="answer")
    _, executor = _run(REPLY_OK_SCRIPT, item, package_context)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "保持未知" in prompt
    assert "不要自行补全前情" in prompt
    assert "【证据状态】" in prompt


def test_false_premise_prompt_forbids_following_wrong_premise(reply_item, package_context):
    item = dict(reply_item, evidence_state="false_premise", desired_policy="correct_premise")
    _, executor = _run(REPLY_OK_SCRIPT, item, package_context)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "错误前提" in prompt
    assert "不要顺着错误前提编造" in prompt
    assert "纠正玩家话里的错误前提" in prompt


def test_withhold_policy_prompt(reply_item, package_context):
    item = dict(reply_item, evidence_state="supported", desired_policy="withhold")
    _, executor = _run(REPLY_OK_SCRIPT, item, package_context)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "不透露秘密" in prompt
    assert "不撒谎" in prompt


def test_supported_prompt_has_no_unknown_constraint(reply_item, package_context):
    item = dict(reply_item, evidence_state="supported", desired_policy="answer")
    _, executor = _run(REPLY_OK_SCRIPT, item, package_context)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "信息充分" in prompt
    assert "保持未知" not in prompt


# ── T10：behaviors 补全（P1-3）──

def test_default_behaviors_filled_for_common_types(qin_plan):
    """casual/romance/emotion/identity 条目获得 strata 默认行为合同（非空）。"""
    for task in ("reply_casual", "reply_romance", "reply_emotion", "reply_identity"):
        items = [i for i in qin_plan.items if i.task_type == task]
        assert items, task
        assert all(i.required_behaviors for i in items), f"{task} required 为空"
        assert all(i.forbidden_behaviors for i in items), f"{task} forbidden 为空"


def test_protective_explicit_behaviors_preserved(qin_plan):
    """protective 条目显式声明的 behaviors 不被 strata 默认覆盖。"""
    prot = [i for i in qin_plan.items if i.task_type == "reply_protective"]
    assert all(i.required_behaviors for i in prot)
    # 显式行为词（pools.yaml 声明）必须原样保留
    texts = " ".join(" ".join(i.required_behaviors) for i in prot)
    assert "withhold_without_lying" in texts or "deflect_without_lying" in texts
    assert "complete_safety_guidance" in texts or "advise_safe_response" in texts


def test_casual_behaviors_enter_generation_prompt(reply_item, package_context):
    """集成：casual 默认 forbidden（属性朗读/共同经历）进入生成 prompt。"""
    item = dict(reply_item, required_behaviors=["承接玩家当前话题"], forbidden_behaviors=["主动朗读身份属性"])
    _, executor = _run(REPLY_OK_SCRIPT, item, package_context)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "承接玩家当前话题" in prompt
    assert "主动朗读身份属性" in prompt
