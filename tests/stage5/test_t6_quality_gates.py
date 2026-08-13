"""T6：质量门最小版（G5 秘密关键词门 + gate decision，2026-08-06）。

覆盖：
- secret_keyword_checker：细节泄漏词命中 → reason；暗示词放行；
- ReleaseQualityGate + G5 注入：泄漏候选 rejected，干净候选 accepted；
- 组合决策落盘结构（gen_qin_v4 导出侧同款）。
大块 A（2026-08-07）：词表由调用方显式传入（core 不内置角色词）。
"""
from __future__ import annotations

import pytest

from data_gen_v4.core.gates import (
    ReleaseQualityGate,
    accepted,
    secret_keyword_checker,
)


# 测试词表（模拟 ProfilePackage.policy_terms.secret_terms 的注入形态）
SECRET_TERMS = (
    "地堡", "裂缝", "异世界", "闯关", "相依为命", "咽气",
    "活过一年", "任务世界", "枯苗", "夏至", "6月21日",
)


def _candidate(messages: list[tuple[str, str]]) -> dict:
    return {
        "target": {
            "messages": [{"role": role, "content": text} for role, text in messages]
        }
    }


@pytest.mark.parametrize(
    "text,term",
    [
        ("我偶尔半夜惊醒以为还在地堡", "地堡"),
        ("城郊废弃防空洞的灰白裂缝", "裂缝"),
        ("那一年我们在异世界", "异世界"),
        ("我们相依为命活过一年", "相依为命"),
    ],
)
def test_g5_rejects_detail_leak_terms(text, term):
    checker = secret_keyword_checker(SECRET_TERMS)
    reasons = checker(_candidate([("human", "h"), ("assistant", text)]), {})
    assert any(r == f"secret_leak:{term}" for r in reasons)


def test_g5_allows_hint_and_scene_words():
    checker = secret_keyword_checker(SECRET_TERMS)
    reasons = checker(
        _candidate([("human", "h"), ("assistant", "灰蒙蒙的天，你以前不是这样的。")]), {}
    )
    assert reasons == []
    reasons = checker(
        _candidate([("human", "h"), ("assistant", "路过的防空洞，好像小时候见过。")]), {}
    )
    assert reasons == []


def test_gate_evaluation_rejects_leak_and_accepts_clean():
    gate = ReleaseQualityGate()
    gate.set_injected("G5", secret_keyword_checker(SECRET_TERMS))
    context = {"lock": {"lock_hash": "x"}, "snapshots": {}, "turn_bounds": (2, 64)}
    leak = _candidate([("human", "h"), ("assistant", "地堡里好冷")])
    decisions = gate.evaluate(leak, context, enabled_gates={"G0", "G1", "G2", "G5", "G6"})
    assert not accepted(decisions)
    g5 = next(d for d in decisions if d.gate_id == "G5")
    assert g5.decision == "rejected"
    assert g5.reason_codes == ["secret_leak:地堡"]

    clean = _candidate(
        [
            ("human", "h1"),
            ("assistant", "今天吃什么"),
            ("human", "h2"),
            ("assistant", "随你呀"),
        ]
    )
    clean["package_lock_hash"] = "x"
    decisions = gate.evaluate(clean, context, enabled_gates={"G5"})
    g5 = next(d for d in decisions if d.gate_id == "G5")
    assert g5.decision == "approved"
