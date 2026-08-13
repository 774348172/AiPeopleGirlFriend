"""PackageResolver：ref 解析、校验、依赖闭合、lock pin。"""
from __future__ import annotations

import pytest

from data_gen_v4.core.errors import PackageIncompatibleError, PackageInvalidError
from data_gen_v4.core.resolver import (
    PackageResolver,
    content_hash_of,
    version_matches,
)
from tests.core.fixtures import (
    FakeRegistry,
    default_registry,
    make_package,
    package_set,
    protocol_bundle,
)


def _resolver(registry=None):
    return PackageResolver(registry or default_registry())


def test_resolve_four_packages():
    resolved = _resolver().resolve(package_set())
    assert resolved.profile["profile_id"] == "fixture.alpha"
    assert resolved.protocol["protocol_bundle_id"] == "protocol:fixture:rel"
    assert resolved.recipe["recipe_id"] == "recipe:fixture:basic"
    assert resolved.release["release_policy_id"] == "release:fixture:basic"


def test_ref_version_mismatch_is_incompatible():
    with pytest.raises(PackageIncompatibleError):
        _resolver().resolve(package_set().__class__(
            profile_package_ref="pkg:profile.fixture.alpha@9.9.9",
            protocol_bundle_ref=package_set().protocol_bundle_ref,
            dataset_recipe_ref=package_set().dataset_recipe_ref,
            release_policy_ref=package_set().release_policy_ref,
        ))


def test_missing_package_is_invalid():
    with pytest.raises(PackageInvalidError):
        _resolver().resolve(package_set().__class__(
            profile_package_ref="pkg:profile.does.not.exist@1.0.0",
            protocol_bundle_ref=package_set().protocol_bundle_ref,
            dataset_recipe_ref=package_set().dataset_recipe_ref,
            release_policy_ref=package_set().release_policy_ref,
        ))


def test_bad_ref_syntax_is_invalid():
    with pytest.raises(PackageInvalidError):
        _resolver().resolve(package_set().__class__(
            profile_package_ref="profile.fixture.alpha@1.0.0",
            protocol_bundle_ref=package_set().protocol_bundle_ref,
            dataset_recipe_ref=package_set().dataset_recipe_ref,
            release_policy_ref=package_set().release_policy_ref,
        ))


def test_draft_package_is_invalid():
    draft = make_package(
        "profile",
        package_id="profile.fixture.draft", schema_version="aip.profile.v4",
        package_version="1.0.0", status="draft", profile_id="fixture.draft",
        locale="zh-CN", identity_sources=[], canon_sources=[],
        visibility_model="v", style_contract="s", disclosure_policy="direct_allowed",
        capability_traits=[], source_validators=[], style_validators=[],
        profile_prompt_fragments=[], profile_evaluation_suites=[],
    )
    registry = default_registry()
    registry._packages["profile.fixture.draft"] = draft
    with pytest.raises(PackageInvalidError):
        _resolver(registry).resolve(package_set().__class__(
            profile_package_ref="pkg:profile.fixture.draft@1.0.0",
            protocol_bundle_ref=package_set().protocol_bundle_ref,
            dataset_recipe_ref=package_set().dataset_recipe_ref,
            release_policy_ref=package_set().release_policy_ref,
        ))


def test_tampered_content_hash_is_invalid():
    registry = default_registry()
    profile = registry.get("profile.fixture.alpha")
    tampered = dict(profile)
    tampered["content_hash"] = "sha256:" + "f" * 64
    registry._packages["profile.fixture.alpha"] = tampered
    with pytest.raises(PackageInvalidError):
        _resolver(registry).resolve(package_set())


def test_core_range_mismatch_is_incompatible():
    registry = default_registry()
    profile = registry.get("profile.fixture.alpha")
    narrowed = dict(profile)
    narrowed["compatible_core_range"] = ">=99.0.0"
    narrowed["content_hash"] = content_hash_of(narrowed)
    registry._packages["profile.fixture.alpha"] = narrowed
    with pytest.raises(PackageIncompatibleError):
        _resolver(registry).resolve(package_set())


def test_dependency_closure_and_cycle_detection():
    leaf = make_package(
        "release", package_id="release.fixture.leaf",
        schema_version="aip.release.v4", package_version="1.0.0",
        release_policy_id="release:fixture:leaf",
        required_core_gates=["G0"], required_profile_validators=[],
        required_protocol_validators=[], human_review_rules=[],
        metric_thresholds={}, sealed_evaluation_refs=[],
        allowed_export_profiles=[],
        curation_policy={"exact_normalization": "NFKC", "ngram_range": [3, 5],
                         "minhash_params": {}, "embedding_model_ref": None,
                         "allowed_dedup_modes": ["exact"]},
        split_policy={"initial_ratios": [0.8, 0.1, 0.1], "max_component_ratio": 0.15},
    )
    parent = make_package(
        "protocol", package_id="protocol.fixture.parent",
        schema_version="aip.protocol.v4", package_version="1.0.0",
        protocol_bundle_id="protocol:fixture:parent",
        modes=[],
        dependencies=[f"pkg:release.fixture.leaf@1.0.0"],
    )
    registry = default_registry()
    registry._packages["release.fixture.leaf"] = leaf
    registry._packages["protocol.fixture.parent"] = parent
    resolved = _resolver(registry).resolve(package_set().__class__(
        profile_package_ref=package_set().profile_package_ref,
        protocol_bundle_ref="pkg:protocol.fixture.parent@1.0.0",
        dataset_recipe_ref=package_set().dataset_recipe_ref,
        release_policy_ref=package_set().release_policy_ref,
    ))
    assert "release.fixture.leaf" in resolved.dependencies
    assert "release.fixture.leaf" in resolved.lock_packages()

    # 环检测
    a = make_package(
        "protocol", package_id="protocol.fixture.cycle-a",
        schema_version="aip.protocol.v4", package_version="1.0.0",
        protocol_bundle_id="protocol:fixture:cycle-a", modes=[],
        dependencies=["pkg:protocol.fixture.cycle-b@1.0.0"],
    )
    b = make_package(
        "protocol", package_id="protocol.fixture.cycle-b",
        schema_version="aip.protocol.v4", package_version="1.0.0",
        protocol_bundle_id="protocol:fixture:cycle-b", modes=[],
        dependencies=["pkg:protocol.fixture.cycle-a@1.0.0"],
    )
    registry._packages["protocol.fixture.cycle-a"] = a
    registry._packages["protocol.fixture.cycle-b"] = b
    with pytest.raises(PackageInvalidError):
        _resolver(registry).resolve(package_set().__class__(
            profile_package_ref=package_set().profile_package_ref,
            protocol_bundle_ref="pkg:protocol.fixture.cycle-a@1.0.0",
            dataset_recipe_ref=package_set().dataset_recipe_ref,
            release_policy_ref=package_set().release_policy_ref,
        ))


def test_lock_packages_only_approved():
    resolved = _resolver().resolve(package_set())
    pins = resolved.lock_packages()
    assert all(pin["status"] == "approved" for pin in pins.values())
    assert pins["profile.fixture.alpha"]["package_version"] == "1.0.0"


@pytest.mark.parametrize(
    ("version", "range_spec", "expected"),
    [
        ("1.0.0", "1.0.0", True),
        ("1.0.1", "1.0.0", False),
        ("1.5.0", ">=1.0.0", True),
        ("0.9.0", ">=1.0.0", False),
        ("2.0.0", ">=1.0.0,<2.0.0", False),
        ("1.9.9", ">=1.0.0,<2.0.0", True),
        ("3.2.1", "*", True),
        ("1.2.3", ">1.2.2", True),
        ("1.2.3", "<=1.2.3", True),
    ],
)
def test_version_matches(version, range_spec, expected):
    assert version_matches(version, range_spec) is expected
