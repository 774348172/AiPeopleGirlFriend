"""阶段 5 E-4（失败回流 behavior_gap 登记）验收。

- 构造/指纹幂等（同 source+family+evidence → 同 gap_id）；
- 登记幂等（重复登记返回 False）；
- 从 ledger 失败提取候选 gap（taxonomy 归类、不产出关键词规则）；
- 非法 source/status 拒绝。
"""
from __future__ import annotations

from data_gen_v4.core.behavior_gap import (
    GapRegistry,
    build_gap,
    extract_gap_candidates,
    gap_fingerprint,
)


def test_gap_fingerprint_idempotent():
    a = gap_fingerprint("failure", "reply_casual", ["rec-1"])
    b = gap_fingerprint("failure", "reply_casual", ["rec-1"])
    assert a == b
    c = gap_fingerprint("failure", "reply_casual", ["rec-2"])
    assert a != c


def test_build_gap_fields():
    gap = build_gap(source="eval_delta", family_id="serious_support",
                    evidence=["case:123"], model_delta={"improved": 1})
    assert gap.gap_id.startswith("bg:serious_support:")
    assert gap.status == "open"
    assert gap.source == "eval_delta"


def test_build_gap_rejects_invalid():
    import pytest

    with pytest.raises(ValueError):
        build_gap(source="nonsense", family_id="f", evidence=[])
    with pytest.raises(ValueError):
        build_gap(source="failure", family_id="f", evidence=[], status="bad")


def test_registry_idempotent(tmp_path):
    path = tmp_path / "gaps.jsonl"
    registry = GapRegistry(path)
    gap = build_gap(source="failure", family_id="reply_casual", evidence=["rec-1"])
    assert registry.register(gap) is True
    assert registry.register(gap) is False  # 幂等
    assert len(registry.all()) == 1
    # 重新加载（持久化）
    registry2 = GapRegistry(path)
    assert len(registry2.all()) == 1


def test_extract_gap_candidates_groups_by_taxonomy(tmp_path):
    failures = [
        {"record_id": "rec-1", "error_code": "style_failure", "reason": "命题无正典支撑: [x]",
         "family_id": "fam:1"},
        {"record_id": "rec-2", "error_code": "style_failure", "reason": "命题无正典支撑: [y]",
         "family_id": "fam:1"},
        {"record_id": "rec-3", "error_code": "parse_error", "reason": "解析失败: 结构错误",
         "family_id": "fam:2"},
    ]
    candidates = extract_gap_candidates(failures)
    by_code = {c["error_code"]: c for c in candidates}
    # style_failure 聚合为 1 个候选（2 条证据）
    assert by_code["style_failure"]["count"] == 2
    assert len(by_code["style_failure"]["evidence"]) == 2
    assert by_code["parse_error"]["count"] == 1
    # 候选 gap 只是聚合（半自动，未自动登记关键词规则）
    assert all("keyword" not in c for c in candidates)


def test_no_keyword_rule_produced(tmp_path):
    """回流约束：登记行为不产出关键词规则（只登记 gap 记录）。"""
    registry = GapRegistry(tmp_path / "g.jsonl")
    gap = build_gap(source="taxonomy", family_id="reply_safety", evidence=["rec-9"],
                    model_delta={"taxonomy": "E4"})
    registry.register(gap)
    content = (tmp_path / "g.jsonl").read_text(encoding="utf-8")
    assert "keyword" not in content  # 无规则产物
