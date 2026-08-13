"""GenerationEngineV4：execute 全流程、resume 幂等恢复、lock/plan 不变量。"""
from __future__ import annotations

import pytest

from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.engine import GenerationEngineV4
from data_gen_v4.core.errors import (
    LockMismatchError,
    ModeNotRegisteredError,
    PreconditionFailedError,
)
from data_gen_v4.core.plan import RunSpec
from data_gen_v4.core.sink import AppendSink, MemorySink
from tests.core.fixtures import (
    FakeItemFactory,
    FakeModelPool,
    FakeModeAdapter,
    FakeSourceLoader,
    alpha_snapshots,
    default_registry,
    package_set,
)


def _compiled(seed: int = 42, run_id: str = "run-1"):
    compiler = GenerationPlanCompiler(
        registry=default_registry(),
        source_loader=FakeSourceLoader(alpha_snapshots()),
        item_factory=FakeItemFactory(),
    )
    return compiler.compile(RunSpec(run_id=run_id, seed=seed), package_set())


def _engine(adapter: FakeModeAdapter | None = None) -> tuple[GenerationEngineV4, FakeModeAdapter]:
    adapter = adapter or FakeModeAdapter()
    return GenerationEngineV4(mode_adapters={"REPLY": adapter}), adapter


def _sink(tmp_path):
    return AppendSink.open(tmp_path / "engine.sqlite")


def test_execute_runs_all_items(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        run = engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        assert run.completed == 3
        assert run.skipped == 0
        assert run.failed == 0
        assert run.generation_completed is True
        progress = sink.read_progress("run-1")
        assert progress.run_started is True
        assert progress.generation_completed is True
        assert len(progress.candidates) == 3
        assert progress.candidates[0].review_status == "pending"
        assert progress.candidates[0].sample_id.startswith("plan-")
        assert progress.candidates[0].header.plan_id.startswith(result.plan.plan_id + ":")


def test_execute_rejects_existing_run(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        with pytest.raises(PreconditionFailedError, match="run 已存在"):
            engine.execute(result.plan, result.lock, FakeModelPool(), sink)


def test_execute_rejects_lock_mismatch(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        bad_lock = dict(result.lock)
        bad_lock["lock_hash"] = "sha256:" + "f" * 64
        with pytest.raises(LockMismatchError):
            engine.execute(result.plan, bad_lock, FakeModelPool(), sink)


def test_execute_missing_mode_adapter_fails(tmp_path):
    result = _compiled()
    engine = GenerationEngineV4(mode_adapters={})
    with _sink(tmp_path) as sink:
        with pytest.raises(ModeNotRegisteredError):
            engine.execute(result.plan, result.lock, FakeModelPool(), sink)


def test_retry_until_success_writes_one_candidate(tmp_path):
    result = _compiled()
    engine, adapter = _engine(FakeModeAdapter(fail_first=1))
    with _sink(tmp_path) as sink:
        run = engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        assert run.completed == 3
        progress = sink.read_progress("run-1")
        # 前 3 个 item 各失败 1 次后成功：3 个 candidate + 3 个 failure
        assert len(progress.candidates) == 3
        assert len(progress.failures) == 3
        assert all(f.retryable for f in progress.failures)
        assert all(c.attempt_no == 2 for c in progress.candidates)


def test_always_fail_respects_max_attempts(tmp_path):
    result = _compiled()
    engine, _ = _engine(FakeModeAdapter(always_fail=True))
    with _sink(tmp_path) as sink:
        run = engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        assert run.completed == 0
        assert run.failed == 3
        progress = sink.read_progress("run-1")
        assert len(progress.candidates) == 0
        assert len(progress.failures) == 3 * 3  # 3 items × max_attempts=3
        assert progress.failed_plan_ids == {
            item.plan_id for item in result.plan.items
        }


def test_resume_skips_completed_items_and_finishes(tmp_path):
    result = _compiled()
    # 第一次 execute：让部分 item 失败（adapter 前 1 次调用抛异常），其余成功
    class CrashOnce(FakeModeAdapter):
        def generate(self, mode_job, call_executor):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("模拟中断")
            return super().generate(mode_job, call_executor)

    engine, _ = _engine(CrashOnce())
    with _sink(tmp_path) as sink:
        try:
            engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        except RuntimeError:
            pass  # 模拟进程中断
        progress = sink.read_progress("run-1")
        assert not progress.generation_completed
        done_before = progress.completed_plan_ids

        # resume：新 engine 实例，正常 adapter
        engine2, _ = _engine()
        run = engine2.resume("run-1", result.plan, result.lock, FakeModelPool(), sink)
        assert run.completed + run.skipped == 3
        assert run.skipped == len(done_before)
        progress = sink.read_progress("run-1")
        assert progress.generation_completed is True
        assert len(progress.candidates) == 3


def test_resume_unknown_run_fails(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        with pytest.raises(PreconditionFailedError, match="run 不存在"):
            engine.resume("run-ghost", result.plan, result.lock, FakeModelPool(), sink)


def test_resume_completed_run_fails(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        with pytest.raises(PreconditionFailedError, match="run 已完成"):
            engine.resume("run-1", result.plan, result.lock, FakeModelPool(), sink)


def test_resume_rejects_different_plan(tmp_path):
    result = _compiled(run_id="run-1")
    other = _compiled(run_id="run-1", seed=999)  # 不同 seed → 不同 items/plan_id

    class CrashAlways(FakeModeAdapter):
        def generate(self, mode_job, call_executor):
            self.calls += 1
            raise RuntimeError("中断")

    engine, _ = _engine(CrashAlways())
    with _sink(tmp_path) as sink:
        with pytest.raises(RuntimeError):
            engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        # run 未完成；换 plan resume 必须被拒绝
        engine2, _ = _engine()
        with pytest.raises(LockMismatchError):
            engine2.resume("run-1", other.plan, other.lock, FakeModelPool(), sink)


def test_call_records_are_durably_appended(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    pool = FakeModelPool()
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, pool, sink)
        assert len(pool.adapter.specs) >= 3  # 每个 candidate 至少 1 次模型调用
        progress = sink.read_progress("run-1")
        records = sink.list_runs()
        assert records == ["run-1"]
        # candidate 的 provenance.calls 指向 call record id
        # 大块 B（阶段 2）：calls 为真实 CallRecord.record_id（rec- 前缀）
        assert progress.candidates[0].provenance["calls"]
        assert progress.candidates[0].provenance["calls"][0].startswith("rec-")


def test_memory_sink_supports_full_execute(tmp_path):
    result = _compiled()
    engine, _ = _engine()
    sink = MemorySink()
    run = engine.execute(result.plan, result.lock, FakeModelPool(), sink)
    assert run.completed == 3
    progress = sink.read_progress("run-1")
    assert len(progress.candidates) == 3
    assert progress.generation_completed is True
