"""隔离性硬标准：#7 交叉污染、#8 删除、#9 不兼容（模型调用前失败）、无 core 分支。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
from data_gen_v4.adapters.sources.registry import (
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.errors import (
    PackageIncompatibleError,
    PackageInvalidError,
    SourceSnapshotFailedError,
)
from data_gen_v4.core.plan import PackageSetV4, RunSpec
from tests.stage3.conftest import (
    ALPHA_SCRIPT,
    BETA_SCRIPT,
    PROFILES_ROOT,
    build_compiler,
    package_set_for,
    run_pipeline,
)

ALPHA_TERMS = ["阿尔法", "河畔公寓", "豆包", "插画师"]
BETA_TERMS = ["贝塔", "图书馆", "图书管理员", "借阅"]


def _candidate_texts(progress) -> str:
    return "".join(
        m["content"] for c in progress.candidates for m in c.target["messages"]
    )


def test_no_cross_contamination_alpha_into_beta(tmp_path):
    """Alpha 的任何事实不得出现在 Beta 候选（硬标准 #7）。"""
    _, beta_progress, _ = run_pipeline("beta", BETA_SCRIPT, tmp_path)
    beta_text = _candidate_texts(beta_progress)
    for term in ALPHA_TERMS:
        assert term not in beta_text, f"Beta 候选出现 Alpha 事实: {term}"


def test_no_cross_contamination_beta_into_alpha(tmp_path):
    _, alpha_progress, _ = run_pipeline("alpha", ALPHA_SCRIPT, tmp_path)
    alpha_text = _candidate_texts(alpha_progress)
    for term in BETA_TERMS:
        assert term not in alpha_text, f"Alpha 候选出现 Beta 事实: {term}"


def test_delete_beta_alpha_still_works(tmp_path):
    """删除 Beta 后，core 与 Alpha 全流程照常（硬标准 #8）。"""
    # 只含 alpha 的 registry（副本目录模拟删除 beta）
    reduced = tmp_path / "profiles_only_alpha"
    shutil.copytree(PROFILES_ROOT, reduced)
    shutil.rmtree(reduced / "fixture.beta")
    registry = FilePackageRegistry(reduced)
    assert registry.list_profiles() == ["fixture.alpha"]
    compiler = build_compiler("alpha", profiles_root=reduced)
    result = compiler.compile(RunSpec(run_id="alpha-only", seed=7), package_set_for("alpha"))
    assert result.plan.profile_id == "fixture.alpha"
    # beta 的 package 也不应存在（删除后解析失败）
    assert registry.get("profile.fixture.beta") is None


def test_cross_profile_source_ref_fails_before_model(tmp_path):
    """Beta 的 canon source 指向不存在的文件（模拟跨 profile 引用）→ 模型调用前失败。"""
    # 副本中把 beta 的 canon source 改为非法路径
    modified = tmp_path / "profiles_modified"
    shutil.copytree(PROFILES_ROOT, modified)
    profile_file = modified / "fixture.beta" / "profile.yaml"
    text = profile_file.read_text(encoding="utf-8")
    profile_file.write_text(
        text.replace('canon_sources: ["file:sources/canon.md"]',
                     'canon_sources: ["file:../../fixture.alpha/sources/canon.json"]'),
        encoding="utf-8",
    )
    registry = FilePackageRegistry(modified)
    compiler = GenerationPlanCompiler(
        registry,
        CompositeSourceLoader(modified / "fixture.beta"),
        RecipeDrivenItemFactory(),
    )
    with pytest.raises(SourceSnapshotFailedError):
        compiler.compile(RunSpec(run_id="bad-ref"), package_set_for("beta"))


def test_version_incompatibility_fails_before_model_call(tmp_path):
    """版本不匹配 → package_incompatible，且 engine 未被调用（硬标准 #8）。"""
    bad_set = PackageSetV4(
        profile_package_ref="pkg:profile.fixture.beta@9.9.9",
        protocol_bundle_ref="pkg:protocol.fixture.rel@1.0.0",
        dataset_recipe_ref="pkg:recipe.fixture.beta@1.0.0",
        release_policy_ref="pkg:release.fixture.beta@1.0.0",
    )
    compiler = build_compiler("beta")
    with pytest.raises(PackageIncompatibleError):
        compiler.compile(RunSpec(run_id="bad-version"), bad_set)


def test_tampered_profile_content_hash_fails_before_model(tmp_path):
    """package 内容被篡改（content_hash 不自洽）→ 加载即失败，模型调用前阻断。"""
    registry = FilePackageRegistry(PROFILES_ROOT)
    profile = registry.get("profile.fixture.alpha")
    assert profile["content_hash"].startswith("sha256:")
    # 篡改副本文件后重新加载必须失败
    modified = tmp_path / "profiles_tampered"
    shutil.copytree(PROFILES_ROOT, modified)
    profile_file = modified / "fixture.alpha" / "profile.yaml"
    text = profile_file.read_text(encoding="utf-8")
    profile_file.write_text(text + "\ncontent_hash: sha256:" + "f" * 64 + "\n", encoding="utf-8")
    tampered = FilePackageRegistry(modified)
    with pytest.raises(PackageInvalidError):
        tampered.get("profile.fixture.alpha")


def test_core_has_no_profile_specific_branch():
    """硬标准 #4/#10：core/adapters/prompts 源码中不得出现 fixture profile 名与事件词。"""
    from data_gen_v4.core.contamination import scan_directory

    core_dir = Path(__file__).resolve().parents[2] / "data_gen_v4"
    sensitive = {"fixture.alpha", "fixture.beta", "阿尔法", "贝塔", "河畔公寓", "图书馆", "豆包"}
    violations = scan_directory(core_dir, sensitive)
    assert violations == [], [str(v) for v in violations]


def test_profile_terms_only_in_test_data():
    """profile 名只允许出现在 tests/ 与 profiles/ 数据目录（不在 core）。"""
    import re

    core_dir = Path(__file__).resolve().parents[2] / "data_gen_v4"
    for path in core_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert "fixture.alpha" not in text, f"core 源码出现 profile 名: {path}"
        assert "fixture.beta" not in text, f"core 源码出现 profile 名: {path}"
