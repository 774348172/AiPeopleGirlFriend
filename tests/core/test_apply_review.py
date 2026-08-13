"""G7 人工审核闭环验收（apply_review 工具 + G7 放行）。

- 复核结果导入 ledger（GateDecisionRecord reviewer=human）；
- 幂等（重复导入跳过）；
- 候选不存在 → missing 统计；
- 导入后 G7 对 full 任务放行（无记录拒绝 / 有 approved 记录通过）。
"""
from __future__ import annotations

import json

from data_gen_v4.core.gates import G7HumanReviewGate
from data_gen_v4.core.sink import AppendSink
from tests.core.test_engine import _compiled, _engine, _sink
from tests.core.fixtures import FakeModelPool
from tools.apply_review import apply_reviews


def _run_with_candidates(tmp_path):
    """编译 + 生成（3 items × 1 候选）→ 返回 (sink_path, run_id)。"""
    from dataclasses import replace

    result = _compiled()
    # 全量生成（含 candidate 记录）
    engine, _ = _engine()
    sink_path = tmp_path / "ledger.sqlite"
    with AppendSink.open(sink_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
    return sink_path, "run-1"


def test_apply_review_writes_gate_decision(tmp_path):
    sink_path, _ = _run_with_candidates(tmp_path)
    with AppendSink.open(sink_path) as sink:
        progress = sink.read_progress("run-1")
        sample_id = progress.candidates[0].sample_id
    reviews = [
        {"sample_id": sample_id, "reviewer": "human-1", "decision": "approved", "note": "OK"}
    ]
    stats = apply_reviews(sink_path, reviews)
    assert stats["applied"] == 1
    with AppendSink.open(sink_path) as sink:
        progress = sink.read_progress("run-1")
        decisions = [d for d in progress.gate_decisions if d.gate_id == "G7"]
        assert len(decisions) == 1
        assert decisions[0].reviewer == "human-1"
        assert decisions[0].decision == "approved"
        assert decisions[0].subject_sample_id == sample_id


def test_apply_review_idempotent(tmp_path):
    sink_path, _ = _run_with_candidates(tmp_path)
    with AppendSink.open(sink_path) as sink:
        sample_id = sink.read_progress("run-1").candidates[0].sample_id
    reviews = [{"sample_id": sample_id, "reviewer": "human-1", "decision": "approved"}]
    assert apply_reviews(sink_path, reviews)["applied"] == 1
    assert apply_reviews(sink_path, reviews)["applied"] == 0  # 幂等跳过


def test_apply_review_missing_candidate(tmp_path):
    sink_path, _ = _run_with_candidates(tmp_path)
    reviews = [{"sample_id": "plan-ghost:a1:c1", "reviewer": "human-1", "decision": "approved"}]
    stats = apply_reviews(sink_path, reviews)
    assert stats["missing"] == 1
    assert stats["applied"] == 0


def test_g7_gate_passes_after_human_review(tmp_path):
    """闭环：导入 approved 复核 → G7 对 full 任务放行。"""
    sink_path, _ = _run_with_candidates(tmp_path)
    with AppendSink.open(sink_path) as sink:
        candidate = sink.read_progress("run-1").candidates[0]
        sample_id = candidate.sample_id
    reviews = [{"sample_id": sample_id, "reviewer": "human-1", "decision": "approved"}]
    apply_reviews(sink_path, reviews)

    with AppendSink.open(sink_path) as sink:
        progress = sink.read_progress("run-1")
        # G7 无记录 → 拒绝；有 approved 记录 → 通过
        candidate_dict = candidate.to_dict()
        gate = G7HumanReviewGate()
        assert gate.evaluate(candidate_dict, {"human_review_required": True, "gate_decisions": []}).decision == "rejected"
        decisions = [
            {
                "subject_candidate_record_id": candidate.header.record_id,
                "reviewer": d.reviewer,
                "decision": d.decision,
            }
            for d in progress.gate_decisions
            if d.gate_id == "G7" and d.subject_sample_id == sample_id
        ]
        result = gate.evaluate(
            candidate_dict,
            {"human_review_required": True, "gate_decisions": decisions},
        )
        assert result.decision == "approved"
