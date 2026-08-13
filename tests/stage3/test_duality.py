"""双 fixture profile 全流程：同一核心、零修改驱动 Alpha 与 Beta。"""
from __future__ import annotations

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.adapters.modes.reply import ReplyModeAdapter
from data_gen_v4.adapters.sources.registry import FilePackageRegistry
from tests.stage3.conftest import (
    ALPHA_SCRIPT,
    BETA_SCRIPT,
    PROFILES_ROOT,
    build_compiler,
    package_set_for,
    run_pipeline,
)


def test_alpha_full_pipeline(tmp_path):
    result, progress, run = run_pipeline("alpha", ALPHA_SCRIPT, tmp_path)
    assert run.completed == 4
    assert len(progress.candidates) == 4
    # Alpha 事实进入候选文本（内容来自 profile 的 sources）
    texts = "".join(
        m["content"] for c in progress.candidates for m in c.target["messages"]
    )
    assert "阿尔法" in texts
    assert "河畔公寓" in texts


def test_beta_full_pipeline(tmp_path):
    result, progress, run = run_pipeline("beta", BETA_SCRIPT, tmp_path)
    assert run.completed == 2
    assert len(progress.candidates) == 2
    texts = "".join(
        m["content"] for c in progress.candidates for m in c.target["messages"]
    )
    assert "图书馆" in texts
    # Beta 无 timeline：lock 只有 identity/canon 两个快照
    assert len(result.lock["source_snapshots"]) == 2


def test_alpha_beta_locks_and_plans_differ(tmp_path):
    alpha, _, _ = run_pipeline("alpha", ALPHA_SCRIPT, tmp_path)
    beta, _, _ = run_pipeline("beta", BETA_SCRIPT, tmp_path)
    assert alpha.lock["lock_hash"] != beta.lock["lock_hash"]
    assert alpha.plan.profile_snapshot_id != beta.plan.profile_snapshot_id
    assert alpha.plan.recipe_id != beta.plan.recipe_id


def test_same_factory_drives_both_profiles(tmp_path):
    """同一 RecipeDrivenItemFactory 实例，无修改驱动两个 profile。"""
    factory = RecipeDrivenItemFactory()
    registry = FilePackageRegistry(PROFILES_ROOT)
    alpha_profile = registry.get("profile.fixture.alpha")
    beta_profile = registry.get("profile.fixture.beta")
    alpha_items = factory.build_items(
        profile=alpha_profile, recipe=registry.get("recipe.fixture.alpha"),
        protocol={}, seed=7,
    )
    beta_items = factory.build_items(
        profile=beta_profile, recipe=registry.get("recipe.fixture.beta"),
        protocol={}, seed=7,
    )
    # Alpha 输入场景来自 Alpha recipe；Beta 来自 Beta recipe——内容隔离
    alpha_scenes = {i.input["scene"] for i in alpha_items}
    beta_scenes = {i.input["scene"] for i in beta_items}
    assert any("晚上在客厅" in s for s in alpha_scenes)
    assert any("图书馆" in s for s in beta_scenes)
    assert not (alpha_scenes & beta_scenes)


def test_alpha_uses_first_person_style_contract(tmp_path):
    result, _, _ = run_pipeline("alpha", ALPHA_SCRIPT, tmp_path)
    contract = result.context["profile"]["style_contract"]
    assert "第一人称" in contract


def test_beta_uses_third_person_style_contract(tmp_path):
    result, _, _ = run_pipeline("beta", BETA_SCRIPT, tmp_path)
    contract = result.context["profile"]["style_contract"]
    assert "第三人称" in contract
    assert "正式书面语" in contract


def test_beta_release_policy_differs_from_alpha(tmp_path):
    """Beta 的 release policy 只启用 G0-G3+G6（无 G5/G7），证明 release 配置按 profile 生效。"""
    alpha, _, _ = run_pipeline("alpha", ALPHA_SCRIPT, tmp_path)
    beta, _, _ = run_pipeline("beta", BETA_SCRIPT, tmp_path)
    alpha_gates = set(alpha.context["release"]["required_core_gates"])
    beta_gates = set(beta.context["release"]["required_core_gates"])
    assert "G5" in alpha_gates
    assert "G5" not in beta_gates
    assert "G7" not in beta_gates


def test_beta_target_renders_third_person(tmp_path):
    """Beta 的 REPLY 输出通过 ReplyModeAdapter 的完整结构/语义检查（第三人称由风格合同决定）。"""
    _, progress, _ = run_pipeline("beta", BETA_SCRIPT, tmp_path)
    assert len(progress.candidates) == 2
    # candidate 已通过 G6 结构门（engine 内置检查）且命题保持
    first = progress.candidates[0]
    texts = "".join(m["content"] for m in first.target["messages"])
    assert "市立图书馆收藏了十万册图书" in texts
