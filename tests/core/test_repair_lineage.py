"""阶段 2 B-3（P0-3 lineage）：parent/child repair 链验收。

- attempt>1 的候选 parent_sample_id 指向同 item 前一 attempt 的失败记录；
- ledger 可重建 parent→child 链（parent 记录不可变）。
"""
from __future__ import annotations

from data_gen_v4.core.sink import AppendSink
from tests.core.test_engine import _compiled, _engine, _sink
from tests.core.fixtures import FakeModelPool, FakeModeAdapter


def test_repair_candidate_links_to_parent_failure(tmp_path):
    """fail_first=1：每个 item 首候选 attempt 1 失败 → attempt 2 成功（repair 链）。"""
    result = _compiled()
    engine, _ = _engine(FakeModeAdapter(fail_first=1))
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-1")
        assert len(progress.candidates) == 3
        for candidate in progress.candidates:
            assert candidate.attempt_no == 2, "repair 候选 attempt=2"
            assert candidate.parent_sample_id, "repair 候选必须带 parent 引用"
            # parent 指向同 item 的 attempt 1 失败记录（ledger 中可查、不可变）
            parent = sink.get_record(candidate.parent_sample_id)
            assert parent is not None, f"parent 记录不存在: {candidate.parent_sample_id}"
            assert parent["record_type"] == "failure"
            assert parent["plan_id"] == candidate.header.plan_id
            assert parent["attempt_no"] == 1
            assert parent["candidate_no"] == candidate.candidate_no


def test_first_attempt_candidate_has_no_parent(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-1")
        for candidate in progress.candidates:
            assert candidate.attempt_no == 1
            assert candidate.parent_sample_id is None


def test_repair_chain_rebuildable_from_ledger(tmp_path):
    """G2 验收：repair 链可追溯 parent→child（按 plan_id 从 ledger 重建）。"""
    result = _compiled()
    engine, _ = _engine(FakeModeAdapter(fail_first=1))
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-1")
        for candidate in progress.candidates:
            chain: list[str] = []
            current: str | None = candidate.header.record_id
            while current is not None:
                record = sink.get_record(current)
                assert record is not None, "链断裂"
                chain.append(record["record_type"])
                current = record.get("parent_sample_id")
            # candidate → failure →（attempt 1 无 parent）
            assert chain == ["candidate", "failure"]
