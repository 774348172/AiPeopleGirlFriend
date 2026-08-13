"""relationship-runtime-v1 默认协议包：注册、schema 引用、双 profile 可编译。"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from data_gen_v4.adapters.sources.registry import FilePackageRegistry
from data_gen_v4.core.compiler import GenerationPlanCompiler
from data_gen_v4.core.plan import PackageSetV4, RunSpec
from tests.stage3.conftest import PROFILES_ROOT, build_compiler

BUNDLE_PATH = (
    Path(__file__).resolve().parents[2] / "data_gen_v4" / "packages" / "protocols"
    / "relationship-runtime-v1.yaml"
)
SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "data_gen_v4" / "schemas" / "mode_v4"


def _load_bundle() -> dict:
    return yaml.safe_load(BUNDLE_PATH.read_text(encoding="utf-8"))


def _bundle_package_set(profile_id: str) -> PackageSetV4:
    return PackageSetV4(
        profile_package_ref=f"pkg:profile.fixture.{profile_id}@1.0.0",
        protocol_bundle_ref="pkg:protocol.relationship.runtime.v1@1.2.0",
        dataset_recipe_ref=f"pkg:recipe.fixture.{profile_id}@1.0.0",
        release_policy_ref=f"pkg:release.fixture.{profile_id}@1.0.0",
    )


def test_bundle_registers_three_modes():
    bundle = _load_bundle()
    assert bundle["protocol_bundle_id"] == "relationship-runtime-v1"
    assert bundle["status"] == "approved"
    modes = {m["mode_id"] for m in bundle["modes"]}
    # 2026-08-09：新增 MEMORY_RERANK（记忆选择器训练数据模式）
    assert modes == {"REPLY", "RECALL_PLAN", "MEMORY_PROPOSE", "MEMORY_RERANK"}


def test_bundle_schema_refs_exist():
    bundle = _load_bundle()
    for mode in bundle["modes"]:
        input_ref = SCHEMAS_DIR / mode["input_schema_ref"].split("/")[-1]
        target_ref = SCHEMAS_DIR / mode["target_schema_ref"].split("/")[-1]
        assert input_ref.exists(), mode["input_schema_ref"]
        assert target_ref.exists(), mode["target_schema_ref"]
        json.loads(input_ref.read_text(encoding="utf-8"))  # 合法 JSON Schema
        json.loads(target_ref.read_text(encoding="utf-8"))


def test_bundle_has_three_distinct_render_profiles():
    bundle = _load_bundle()
    profiles = {m["render_profile_id"] for m in bundle["modes"]}
    assert profiles == {
        "reply-runtime-v1", "recall-plan-v1", "memory-propose-v1", "memory-rerank-v1",
    }


def _bundle_compiler(profile_id: str):
    """带默认协议包的 registry（extras：data_gen_v4/packages/protocols/）。"""
    from data_gen_v4.adapters.modes.factory import RecipeDrivenItemFactory
    from data_gen_v4.adapters.sources.registry import CompositeSourceLoader

    registry = FilePackageRegistry(PROFILES_ROOT, extra_package_files=[BUNDLE_PATH])
    loader = CompositeSourceLoader(PROFILES_ROOT / f"fixture.{profile_id}")
    return GenerationPlanCompiler(registry, loader, RecipeDrivenItemFactory())


def test_compiler_accepts_default_bundle_for_alpha(tmp_path):
    """默认 bundle 与 fixture bundle 并行可用：Alpha 用它编译成功。"""
    compiler = _bundle_compiler("alpha")
    result = compiler.compile(
        RunSpec(run_id="bundle-alpha"), _bundle_package_set("alpha")
    )
    assert result.plan.protocol_bundle_id == "relationship-runtime-v1"
    # lock 中包含默认 bundle 的 pin
    assert "protocol.relationship.runtime.v1" in result.lock["packages"]


def test_compiler_accepts_default_bundle_for_beta(tmp_path):
    compiler = _bundle_compiler("beta")
    result = compiler.compile(RunSpec(run_id="bundle-beta"), _bundle_package_set("beta"))
    assert result.plan.protocol_bundle_id == "relationship-runtime-v1"


def test_bundle_does_not_affect_fixture_bundle(tmp_path):
    """fixture 协议包仍然可用（默认包不是核心硬编码）。"""
    from tests.stage3.conftest import package_set_for

    compiler = build_compiler("alpha")
    result = compiler.compile(
        RunSpec(run_id="fixture-bundle"), package_set_for("alpha")
    )
    assert result.plan.protocol_bundle_id == "protocol:fixture:rel"
