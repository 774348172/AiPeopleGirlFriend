"""阶段 5 E-3（晋升判定 7 条）验收。

- 全过场景 → promoted；
- 单项失败（safety blocker/contamination/近重复/seed/通用/盲测/manifest）→ 拒绝；
- 数据缺失 → data_missing（诚实失败，不假通过）。
"""
from __future__ import annotations

from data_gen_v4.core.promotion import PromotionGate


def _full_evidence() -> dict:
    return {
        "safety": {"blockers": 0, "total": 12},
        "contamination": {"findings": 0, "status": "passed"},
        "split": {"cross_split_near_duplicates": 0},
        "seed_deltas": [
            {"improved": 3, "regressed": 1},
            {"improved": 2, "regressed": 1},
            {"improved": 2, "regressed": 1},
        ],
        "general": {"misrefusal_rate": 0.02, "regressed_ratio": 0.01},
        "human_blind": {"wins": 5, "total": 8, "baseline_wins": 4},
        "manifest": {"rebuildable": True, "sample_ids": ["a", "b"], "ledger_rebuilt": True},
    }


def test_all_conditions_pass():
    report = PromotionGate().evaluate(_full_evidence())
    assert report.promoted is True
    assert all(r.passed for r in report.results)


def test_safety_blocker_fails():
    evidence = _full_evidence()
    evidence["safety"] = {"blockers": 1, "total": 12}  # 通过率 91.7% < 100%
    report = PromotionGate().evaluate(evidence)
    assert report.promoted is False
    c1 = next(r for r in report.results if r.condition == 1)
    assert c1.status == "failed"


def test_contamination_fails():
    evidence = _full_evidence()
    evidence["contamination"] = {"findings": 2, "status": "blocked"}
    report = PromotionGate().evaluate(evidence)
    assert not report.promoted
    assert next(r for r in report.results if r.condition == 2).status == "failed"


def test_split_leakage_fails():
    evidence = _full_evidence()
    evidence["split"] = {"cross_split_near_duplicates": 3}
    report = PromotionGate().evaluate(evidence)
    assert not report.promoted
    assert next(r for r in report.results if r.condition == 3).status == "failed"


def test_family_seed_fails():
    evidence = _full_evidence()
    evidence["seed_deltas"] = [
        {"improved": 3, "regressed": 1},
        {"improved": 1, "regressed": 2},
        {"improved": 1, "regressed": 3},
    ]  # 仅 1/3 seed 改善
    report = PromotionGate().evaluate(evidence)
    assert not report.promoted
    assert next(r for r in report.results if r.condition == 4).status == "failed"


def test_general_misrefusal_fails():
    evidence = _full_evidence()
    evidence["general"] = {"misrefusal_rate": 0.2, "regressed_ratio": 0.01}  # 误拒率超 5%
    report = PromotionGate().evaluate(evidence)
    assert not report.promoted
    assert next(r for r in report.results if r.condition == 5).status == "failed"


def test_human_blind_fails():
    evidence = _full_evidence()
    evidence["human_blind"] = {"wins": 3, "total": 8, "baseline_wins": 4}  # 低于基线
    report = PromotionGate().evaluate(evidence)
    assert not report.promoted
    assert next(r for r in report.results if r.condition == 6).status == "failed"


def test_manifest_rebuild_fails():
    evidence = _full_evidence()
    evidence["manifest"] = {"rebuildable": False, "sample_ids": [], "ledger_rebuilt": False}
    report = PromotionGate().evaluate(evidence)
    assert not report.promoted
    assert next(r for r in report.results if r.condition == 7).status == "failed"


def test_missing_data_is_honest_failure():
    report = PromotionGate().evaluate({})  # 全部数据缺失
    assert report.promoted is False
    assert all(r.status == "data_missing" for r in report.results)
