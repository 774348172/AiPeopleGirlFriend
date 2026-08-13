"""GenerationPlanCompiler：编译成功、失败语义与 lock 自洽。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.errors import (
    ModeNotRegisteredError,
    PreconditionFailedError,
    SourceSnapshotFailedError,
)
from data_gen_v4.core.plan import RunSpec
from tests.core.fixtures import (
    FakeItemFactory,
    FakeSourceLoader,
    alpha_snapshots,
    beta_snapshots,
    default_registry,
    make_package,
    package_set,
)


def _compiler(registry=None, loader=None, factory=None):
    return GenerationPlanCompiler(
        registry=registry or default_registry(),
        source_loader=loader or FakeSourceLoader(alpha_snapshots()),
        item_factory=factory or FakeItemFactory(),
    )


def test_compile_success_returns_plan_and_self_consistent_lock():
    result = _compiler().compile(RunSpec(run_id="run-1"), package_set())
    assert result.plan.package_lock_hash == result.lock["lock_hash"]
    assert result.plan.profile_id == "fixture.alpha"
    assert result.plan.recipe_id == "recipe:fixture:basic"
    assert len(result.plan.items) == 3


def test_compile_seed_derivation_is_deterministic_per_family():
    first = _compiler().compile(RunSpec(run_id="run-1", seed=42), package_set())
    second = _compiler().compile(RunSpec(run_id="run-2", seed=42), package_set())
    by_family = {item.family_id: item.seed for item in first.plan.items}
    by_family2 = {item.family_id: item.seed for item in second.plan.items}
    assert by_family == by_family2
    assert len(set(by_family.values())) == 2  # 两个 family 各自不同 seed
    # 同一 family 的对照样本共享 seed（对照只改变一个变量）
    roles = {i.family_role: i.seed for i in first.plan.items if i.family_id == "fam:1"}
    assert roles["positive_control"] == roles["boundary_variant"]


def test_compile_split_anchors_are_initialised():
    result = _compiler().compile(RunSpec(run_id="run-1"), package_set())
    item = result.plan.items[0]
    assert "family:fam:1" in item.split_anchor_ids
    assert "question_family:qf:casual" in item.split_anchor_ids


def test_compile_changing_profile_changes_lock():
    # 把 profile.fixture.alpha 包替换为 beta 内容（包 id 不变），loader 换 beta 来源
    replaced = make_package(
        "profile", package_id="profile.fixture.alpha", schema_version="aip.profile.v4",
        package_version="1.0.0", profile_id="fixture.beta", locale="zh-CN",
        identity_sources=["source:beta:identity"], canon_sources=["source:beta:canon"],
        timeline_sources=[], visibility_model="visibility:public-only-v1",
        style_contract="style:third-person-formal-v1", disclosure_policy="direct_allowed",
        label_schema=None, capability_traits=[], source_validators=[], style_validators=[],
        profile_prompt_fragments=[], profile_evaluation_suites=[],
    )
    registry = default_registry()
    registry._packages["profile.fixture.alpha"] = replaced
    result_a = _compiler().compile(RunSpec(run_id="r1"), package_set())
    result_b = _compiler(registry=registry, loader=FakeSourceLoader(beta_snapshots())).compile(
        RunSpec(run_id="r2"), package_set()
    )
    assert result_a.lock["lock_hash"] != result_b.lock["lock_hash"]
    assert result_b.plan.profile_id == "fixture.beta"


def test_compile_quota_shortfall_fails():
    class ShortFactory(FakeItemFactory):
        def build_items(self, **kwargs):
            return super().build_items(**kwargs)[:1]  # 只有 1 个 item < target 2

    with pytest.raises(PreconditionFailedError, match="配额不足"):
        _compiler(factory=ShortFactory()).compile(RunSpec(run_id="r"), package_set())


def test_compile_unregistered_mode_fails():
    class BadModeFactory(FakeItemFactory):
        def build_items(self, **kwargs):
            items = super().build_items(**kwargs)
            return [replace(items[0], mode="RECALL_PLAN")]

    with pytest.raises(ModeNotRegisteredError):
        _compiler(factory=BadModeFactory()).compile(RunSpec(run_id="r"), package_set())


def test_compile_contrast_pair_incomplete_fails():
    class LoneControlFactory(FakeItemFactory):
        def build_items(self, **kwargs):
            items = super().build_items(**kwargs)
            return [i for i in items if i.family_role != "boundary_variant"]

    with pytest.raises(PreconditionFailedError, match="contrast family"):
        _compiler(factory=LoneControlFactory()).compile(RunSpec(run_id="r"), package_set())


def test_compile_missing_source_fails():
    loader = FakeSourceLoader(alpha_snapshots())
    loader._snapshots.pop("source:alpha:canon")
    with pytest.raises(SourceSnapshotFailedError):
        _compiler(loader=loader).compile(RunSpec(run_id="r"), package_set())


def test_compile_snapshot_unit_mismatch_fails():
    snapshots = alpha_snapshots()
    snapshots["source:alpha:identity"]["units"][0]["snapshot_id"] = "snap:other"
    with pytest.raises(SourceSnapshotFailedError):
        _compiler(loader=FakeSourceLoader(snapshots)).compile(RunSpec(run_id="r"), package_set())


def test_compile_produces_no_partial_plan_on_failure():
    class BadModeFactory(FakeItemFactory):
        def build_items(self, **kwargs):
            items = super().build_items(**kwargs)
            return [replace(items[0], mode="NOPE")]

    with pytest.raises(ModeNotRegisteredError):
        _compiler(factory=BadModeFactory()).compile(RunSpec(run_id="r"), package_set())
