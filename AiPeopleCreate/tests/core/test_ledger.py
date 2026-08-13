"""阶段 2 B-1（P0-3 lineage 成立）：ledger 记录层验收。

- CallRecord 保存原始外部响应 content（可重放，不重新请求）；
- transient provider error 也落盘（finish_reason=error:，content 空）；
- candidate.provenance.calls 回链真实 CallRecord.record_id（可查原始响应）。
"""
from __future__ import annotations

from data_gen_v4.core.engine import CallExecutor
from data_gen_v4.core.records import CallRecord, CandidateRecordV4
from data_gen_v4.core.sink import AppendSink
from tests.core.test_engine import _compiled, _engine, _sink
from tests.core.fixtures import FakeModelPool, FakeModeAdapter


def test_call_record_persists_raw_content(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-1")
        candidate = progress.candidates[0]
        call_ids = candidate.provenance["calls"]
        assert call_ids, "candidate 必须回链 call 记录"
        call = sink.get_record(call_ids[0])
        assert call is not None
        assert call["record_type"] == "call"
        assert call["content"] == "好的。"  # FakeModelAdapter 原始响应全文
        assert call["finish_reason"] == "stop"
        assert call["attempt_no"] == 1
        assert call["candidate_no"] == 1


def test_transient_failure_calls_persisted(tmp_path):
    """CallExecutor 失败尝试也落盘（finish_reason=error:，content 空）。"""
    from data_gen_v4.core.errors import V4Error, V4ErrorCode
    from tests.core.test_error_retry_classification import _Header, _Sink, _FakePool

    class _FailingPool(_FakePool):
        def generate(self, spec):
            raise V4Error("boom", code=V4ErrorCode.PROVIDER_ERROR)

    pool = _FailingPool([])
    executor = CallExecutor(
        pool, _Sink(), _Header(),
        max_consecutive_failures=2, retry_base_delay_seconds=0,
    )
    try:
        executor.call({"prompt_hash": "h"}, stage="semantic", attempt_no=1, candidate_no=1)
    except V4Error:
        pass
    # _Sink 是丢弃式——此处验证 record_ids 收集了失败调用
    assert executor.record_ids(), "失败调用必须产生 CallRecord（record_ids 非空）"


def test_call_record_ids_flow_to_candidate_provenance(tmp_path):
    """engine 全链路：candidate.provenance.calls 指向 sink 中的 call 记录。"""
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-1")
        for candidate in progress.candidates:
            for call_id in candidate.provenance["calls"]:
                record = sink.get_record(call_id)
                assert record is not None, f"candidate 引用的 call 记录不存在: {call_id}"
                assert record["plan_id"] == candidate.header.plan_id
