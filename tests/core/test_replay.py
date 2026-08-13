"""阶段 2 B-4（P1-5，G2 验收）：ledger 重放。

- 中断后 resume：已完成 candidate 0 重新请求（模型调用不重复）；
- 缺失 candidate_no 补生成（per-candidate 恢复）。
"""
from __future__ import annotations

import pytest

from data_gen_v4.core.sink import AppendSink
from tests.core.test_engine import _compiled, _engine, _sink
from tests.core.fixtures import FakeModelPool, FakeModeAdapter


class _CrashAfterAdapter(FakeModeAdapter):
    """第 crash_at 次 generate 抛 RuntimeError（模拟进程崩溃，execute 中断）。"""

    def __init__(self, crash_at: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self.crash_at = crash_at

    def generate(self, mode_job, call_executor):
        if self.calls + 1 == self.crash_at:
            self.calls += 1
            raise RuntimeError("模拟崩溃")
        return super().generate(mode_job, call_executor)


def test_resume_replays_completed_candidates_with_zero_requests(tmp_path):
    """K=2 × 3 items：第 4 次调用崩溃 → resume 补缺失候选，已完成候选 0 重新请求。"""
    from dataclasses import replace

    result = _compiled()
    items = [replace(item, candidate_count=2) for item in result.plan.items]
    result = replace(result, plan=replace(result.plan, items=items))
    engine, adapter = _engine(_CrashAfterAdapter(crash_at=4))
    sink_path = tmp_path / "replay.sqlite"
    with AppendSink.open(sink_path) as sink:
        with pytest.raises(RuntimeError, match="模拟崩溃"):
            engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        calls_after_crash = adapter.calls
        assert calls_after_crash == 4  # item1(2) + item2 c1(1) + item2 c2 崩溃(1)

    # resume：已完成的 (item1 c1/c2, item2 c1) 跳过（0 重新请求），
    # 缺失的 item2 c2 + item3 c1/c2 补生成
    engine2, adapter2 = _engine()
    with AppendSink.open(sink_path) as sink:
        run = engine2.resume("run-1", result.plan, result.lock, FakeModelPool(), sink)
        assert run.completed == 2  # item2 补 c2 + item3 全量
        assert run.skipped == 1  # item1 两候选已完成
        assert run.failed == 0
        assert adapter2.calls == 3  # 只补 3 次新调用（已完成候选未重新请求）
        progress = sink.read_progress("run-1")
        assert len(progress.candidates) == 6  # 3 items × 2 候选
        assert len(progress.completed_candidate_keys) == 6
        # 全部候选的调用记录仍在 ledger（可重放）
        for candidate in progress.candidates:
            for call_id in candidate.provenance["calls"]:
                assert sink.get_record(call_id) is not None


def test_resume_missing_candidate_no_is_backfilled(tmp_path):
    """item 完成 1 个候选后中断 → resume 补缺失 candidate_no。"""
    from dataclasses import replace

    result = _compiled()
    items = [replace(item, candidate_count=2) for item in result.plan.items]
    result = replace(result, plan=replace(result.plan, items=items))
    engine, _ = _engine(_CrashAfterAdapter(crash_at=2))  # item1 c2 处崩溃
    sink_path = tmp_path / "replay2.sqlite"
    with AppendSink.open(sink_path) as sink:
        with pytest.raises(RuntimeError):
            engine.execute(result.plan, result.lock, FakeModelPool(), sink)
    engine2, adapter2 = _engine()
    with AppendSink.open(sink_path) as sink:
        engine2.resume("run-1", result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-1")
        # item1 c1 已完成（0 重新请求），c2 补生成，其余 item 全量
        assert adapter2.calls == 5  # item1 c2(1) + item2(2) + item3(2)
        assert len(progress.candidates) == 6
