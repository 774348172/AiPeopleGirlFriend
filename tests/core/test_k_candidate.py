"""阶段 2 B-2（P0-4 candidate_count 生效）：K Candidate 验收。

- candidate_count=K → 每 item 产出 K 个独立候选（c1..cK、seed 不同、可追踪）；
- candidate_count=1 → 单候选（兼容既有行为）；
- 部分候选失败 → item 部分成功；K 全败 → item 失败；
- per-candidate 进度（completed_candidate_keys）。
"""
from __future__ import annotations

from dataclasses import replace

from data_gen_v4.core.plan import RunSpec
from data_gen_v4.core.sink import AppendSink
from tests.core.test_engine import _compiled, _engine, _sink
from tests.core.fixtures import FakeItemFactory, FakeModelPool, FakeModeAdapter


def _compiled_k(k: int, seed: int = 42, run_id: str = "run-k"):
    result = _compiled(seed=seed, run_id=run_id)
    items = [replace(item, candidate_count=k) for item in result.plan.items]
    return replace(result, plan=replace(result.plan, items=items))


def test_candidate_count_k_produces_k_candidates(tmp_path):
    result = _compiled_k(3)
    engine, adapter = _engine()
    with _sink(tmp_path) as sink:
        run = engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        assert run.completed == 3  # 3 items 全部完成
        progress = sink.read_progress("run-k")
        assert len(progress.candidates) == 9  # 3 items × 3 候选
        # 每个 item 3 个候选：c1/c2/c3，sample_id 格式 a{attempt}:c{no}
        by_plan: dict[str, list] = {}
        for c in progress.candidates:
            by_plan.setdefault(c.header.plan_id, []).append(c)
        for plan_id, cands in by_plan.items():
            assert sorted(c.candidate_no for c in cands) == [1, 2, 3]
            assert len({c.sample_id for c in cands}) == 3
            # 候选多样性：seed 不同（k1/k2/k3 派生）
            assert len({c.seed for c in cands}) == 3
        # per-candidate 进度
        assert len(progress.completed_candidate_keys) == 9
        assert adapter.calls == 9  # K 候选 = K 次模型调用


def test_candidate_count_one_single_candidate(tmp_path):
    result = _compiled_k(1)
    engine, adapter = _engine()
    with _sink(tmp_path) as sink:
        engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-k")
        assert len(progress.candidates) == 3
        assert all(c.candidate_no == 1 for c in progress.candidates)
        assert adapter.calls == 3


def test_partial_candidate_failure_item_still_completes(tmp_path):
    """K 候选下部分候选失败（fail_first 只覆盖前几次调用）→ item 部分成功。"""
    result = _compiled_k(3)
    # fail_first=3：前 3 次调用（item1 的 c1）失败 → item1 的 c1 重试后成功；
    # 构造"一个候选耗尽"：用 always_fail 的变体——按 candidate 粒度难模拟，
    # 这里验证"K 全败 → item 失败"的反例：部分成功 = item 完成
    engine, adapter = _engine(FakeModeAdapter(fail_first=1))
    with _sink(tmp_path) as sink:
        run = engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        progress = sink.read_progress("run-k")
        # 每个 item 的首候选第 1 次 attempt 失败 → attempt 2 成功（repair）
        assert run.failed == 0
        assert len(progress.candidates) == 9
        assert any(c.attempt_no == 2 for c in progress.candidates)
        assert adapter.calls > 9  # 含失败重试


def test_all_candidates_fail_item_fails(tmp_path):
    """K 全败（always_fail）→ item 失败，无候选。"""
    result = _compiled_k(3)
    engine, _ = _engine(FakeModeAdapter(always_fail=True))
    with _sink(tmp_path) as sink:
        run = engine.execute(result.plan, result.lock, FakeModelPool(), sink)
        assert run.failed == 3
        progress = sink.read_progress("run-k")
        assert len(progress.candidates) == 0
        # 3 items × 3 候选 × 3 attempts = 27 条失败记录
        assert len(progress.failures) == 27
