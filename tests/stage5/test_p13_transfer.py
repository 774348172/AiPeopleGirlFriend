"""P1-3 risk/review/candidate 传递链（块 1.4）验收。

修复 factory._build_from_pools 不落 stratum 配置的问题：
- safety 24 条编译为 risk=high / review=full / candidate_count=2（不再 low/auto 失真）；
- protective candidate_count=3；
- 条目显式声明优先；
- recipe 字段名 review_requirement ↔ PlanItem required_review 映射统一。
G7 真拦截（required_review=full → human_review_required）单测在 tests/core/test_gates.py。
"""
from __future__ import annotations

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.core.plan import RunSpec
from tests.stage5.test_qin_pipeline import QWX_PACKAGE_SET, qin_compiler


def _build(recipe: dict, profile: dict | None = None) -> list:
    return RecipeDrivenItemFactory().build_items(
        profile=profile or {}, recipe=recipe, protocol={}, seed=42
    )


def _stratum(**extra) -> dict:
    base = {
        "task_type": "safety",
        "mode_id": "REPLY",
        "target_count": 1,
        "candidate_count": 2,
        "risk_level": "high",
        "review_requirement": "full",
    }
    base.update(extra)
    return base


def test_stratum_config_flows_to_item():
    items = _build(
        {
            "strata": [_stratum()],
            "topic_pools": {"safety": [{"topic": "煤气泄漏", "scene": "厨房"}]},
        }
    )
    assert len(items) == 1
    item = items[0]
    assert item.risk_level == "high"
    assert item.required_review == "full"
    assert item.candidate_count == 2


def test_entry_explicit_values_win_over_stratum():
    items = _build(
        {
            "strata": [_stratum(risk_level="high")],
            "topic_pools": {
                "safety": [{"topic": "t", "scene": "s", "risk_level": "low"}]
            },
        }
    )
    assert items[0].risk_level == "low"  # 条目显式声明优先


def test_required_review_accepts_both_field_names():
    # recipe 用 review_requirement；条目也可用 required_review（schema 侧字段）
    items = _build(
        {
            "strata": [_stratum(review_requirement="family_sample")],
            "topic_pools": {
                "safety": [{"topic": "t", "scene": "s", "required_review": "cluster_sample"}]
            },
        }
    )
    assert items[0].required_review == "cluster_sample"  # 条目显式优先
    items2 = _build(
        {
            "strata": [_stratum(review_requirement="family_sample")],
            "topic_pools": {"safety": [{"topic": "t", "scene": "s"}]},
        }
    )
    assert items2[0].required_review == "family_sample"  # stratum 映射


# ───────────────────────── 真实 profile 集成（P1-3 验收） ─────────────────────────

def test_real_recipe_safety_protective_config(qin_compiler):
    result = qin_compiler.compile(RunSpec(run_id="p13-real", seed=42), QWX_PACKAGE_SET)
    items = result.plan.items

    safety = [i for i in items if i.task_type == "reply_safety"]
    protective = [i for i in items if i.task_type == "reply_protective"]
    casual = [i for i in items if i.task_type == "reply_casual"]
    assert safety, "recipe 必须含 reply_safety"
    assert protective, "recipe 必须含 reply_protective"

    # safety 24 条：high/full/2（修复 low/auto 失真）
    for item in safety:
        assert item.risk_level == "high", f"{item.family_id} risk={item.risk_level}"
        assert item.required_review == "full", f"{item.family_id} review={item.required_review}"
        assert item.candidate_count == 2, f"{item.family_id} count={item.candidate_count}"
    # protective：条目显式 risk（high/medium/low 场景分级，内容作者声明优先），
    # review=full 全显式、candidate_count=3 由 stratum 兜底（P0-4 关联不再丢失）
    for item in protective:
        assert item.risk_level in ("high", "medium", "low"), f"{item.family_id} risk={item.risk_level}"
        assert item.required_review == "full", f"{item.family_id} review={item.required_review}"
        assert item.candidate_count == 3, f"{item.family_id} count={item.candidate_count}"
    # casual：非 full（review=family_sample），不误伤普通任务
    assert casual
    assert all(i.required_review != "full" for i in casual)


def test_full_review_items_are_gated_when_evaluated(qin_compiler):
    """集成：full 任务按 gen_qin_v4 导出段逻辑声明 human_review_required → G7 拦截。"""
    from data_gen_v4.core.gates import G7HumanReviewGate
    from tests.core.test_gates import _candidate_dict

    result = qin_compiler.compile(RunSpec(run_id="p13-g7", seed=42), QWX_PACKAGE_SET)
    safety_item = next(i for i in result.plan.items if i.task_type == "reply_safety")
    assert safety_item.required_review == "full"

    # 模拟导出段：required_review == "full" → human_review_required=True
    context = {"human_review_required": True, "gate_decisions": []}
    candidate = _candidate_dict()
    gate = G7HumanReviewGate()
    assert gate.evaluate(candidate, context).decision == "rejected"
    assert "human_review_missing" in gate.evaluate(candidate, context).reason_codes

    # 人工审核记录落盘后放行（tools/build_review_sheet 复核链路的对接点）
    decisions = [
        {
            "subject_candidate_record_id": candidate["record_id"],
            "reviewer": "human-1",
            "decision": "approved",
        }
    ]
    result2 = G7HumanReviewGate().evaluate(
        candidate, {"human_review_required": True, "gate_decisions": decisions}
    )
    assert result2.decision == "approved"
