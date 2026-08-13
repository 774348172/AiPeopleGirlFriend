"""阶段 5 E-2（评测度量）验收。

- 误拒率计算（general_capability 拒答判定）；
- paired delta 解析 + 晋升组聚合（target_families/safety/general）；
- 多 seed 聚合（≥2/3 seed 稳定改善判定）。
"""
from __future__ import annotations

from data_gen_v4.core.eval_metrics import (
    group_delta,
    misrefusal_rate,
    parse_paired_delta,
    seed_consistency,
    seed_stable_improvement,
)


def test_misrefusal_rate():
    cases = [
        {"case_id": "c1", "category": "general_capability", "output": "答案是 42。"},
        {"case_id": "c2", "category": "general_capability", "output": "不知道。"},  # 拒答
        {"case_id": "c3", "category": "general_capability", "output": ""},  # 空输出 = 拒答
        {"case_id": "c4", "category": "safety_health", "output": "不知道。"},  # 非 general 不计
    ]
    rate = misrefusal_rate(cases)
    assert rate == 2 / 3  # 3 条可回答题中 2 条拒答


def test_misrefusal_rate_no_cases():
    assert misrefusal_rate([]) == 0.0


def test_parse_paired_delta_groups():
    pairs = [
        {"case_id": "a", "category": "identity_timeline", "status": "improved"},
        {"case_id": "b", "category": "relationship_boundary", "status": "regressed"},
        {"case_id": "c", "category": "safety_health", "status": "improved"},
        {"case_id": "d", "category": "general_capability", "status": "regressed"},
        {"case_id": "e", "category": "general_capability", "status": "unchanged"},
        {"case_id": "f", "category": "unknown_category", "status": "improved"},  # 不计
    ]
    summary = parse_paired_delta(pairs)
    # parse 统计全部行（含 unknown_category）；group_delta 才按组过滤
    assert summary.improved == 3 and summary.regressed == 2 and summary.unchanged == 1
    groups = group_delta(summary)
    assert groups["target_families"] == {"improved": 1, "regressed": 1, "unchanged": 0}
    assert groups["safety"] == {"improved": 1, "regressed": 0, "unchanged": 0}
    assert groups["general"] == {"improved": 0, "regressed": 1, "unchanged": 1}


def test_seed_stable_improvement():
    # 3 seed 中 2 个改善 → 通过（≥2/3）
    deltas = [
        {"improved": 3, "regressed": 1},
        {"improved": 2, "regressed": 1},
        {"improved": 1, "regressed": 2},  # 第 3 个退化
    ]
    assert seed_stable_improvement(deltas, min_seeds=2) is True
    # 只有 1 个改善 → 不通过
    bad = [
        {"improved": 3, "regressed": 1},
        {"improved": 1, "regressed": 2},
        {"improved": 1, "regressed": 3},
    ]
    assert seed_stable_improvement(bad, min_seeds=2) is False


def test_seed_consistency_report():
    deltas = [
        {"improved": 3, "regressed": 1},
        {"improved": 2, "regressed": 1},
        {"improved": 1, "regressed": 2},
    ]
    report = seed_consistency(deltas)
    assert report["seeds"] == 3
    assert report["improved_seeds"] == 2
    assert report["stable_improvement"] is True
