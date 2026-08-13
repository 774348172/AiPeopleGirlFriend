"""plan 数据结构：序列化/反序列化、build 填充与 schema 校验。"""
from __future__ import annotations

import pytest

from data_gen_v4.core.errors import SchemaValidationError
from data_gen_v4.core.plan import GenerationPlanV4, PlanItemV4
from tests.core.fixtures import _base_item


def _plan(items: list[PlanItemV4] | None = None) -> GenerationPlanV4:
    return GenerationPlanV4.build(
        run_id="run-1",
        profile_id="fixture.alpha",
        profile_snapshot_id="sha256:" + "b" * 64,
        protocol_bundle_id="protocol:fixture:rel",
        recipe_id="recipe:fixture:basic",
        package_lock_hash="sha256:" + "a" * 64,
        items=items or [_base_item(family_id="fam:1", family_role="standalone")],
    )


def test_plan_roundtrip():
    plan = _plan()
    restored = GenerationPlanV4.from_dict(plan.to_dict())
    assert restored == plan


def test_build_assigns_item_level_plan_ids():
    plan = _plan(
        [
            _base_item(family_id="fam:1", family_role="positive_control"),
            _base_item(family_id="fam:1", family_role="boundary_variant"),
        ]
    )
    ids = [item.plan_id for item in plan.items]
    assert len(set(ids)) == 2
    assert all(pid.startswith(plan.plan_id + ":") for pid in ids)
    # 确定性：同一输入重建得到相同编号
    plan2 = GenerationPlanV4.build(
        run_id="run-1", profile_id="fixture.alpha",
        profile_snapshot_id="sha256:" + "b" * 64,
        protocol_bundle_id="protocol:fixture:rel", recipe_id="recipe:fixture:basic",
        package_lock_hash="sha256:" + "a" * 64,
        items=[
            _base_item(family_id="fam:1", family_role="positive_control"),
            _base_item(family_id="fam:1", family_role="boundary_variant"),
        ],
        plan_id=plan.plan_id,
    )
    assert [item.plan_id for item in plan2.items] == ids


def test_build_fills_lineage_fields_into_items():
    plan = _plan()
    item = plan.items[0]
    assert item.profile_id == "fixture.alpha"
    assert item.profile_snapshot_id == "sha256:" + "b" * 64
    assert item.protocol_bundle_id == "protocol:fixture:rel"
    assert item.recipe_id == "recipe:fixture:basic"


def test_plan_without_items_is_rejected():
    with pytest.raises(SchemaValidationError):
        GenerationPlanV4.build(
            run_id="r", profile_id="p", profile_snapshot_id="s",
            protocol_bundle_id="pb", recipe_id="rc",
            package_lock_hash="sha256:" + "a" * 64, items=[],
        )


def test_refusal_required_is_required_field():
    item = _base_item(family_id="fam:1", family_role="standalone")
    data = item.to_dict()
    del data["refusal_required"]
    with pytest.raises(SchemaValidationError):
        PlanItemV4.from_dict(data)


def test_visibility_scope_min_one():
    item = _base_item(family_id="fam:1", family_role="standalone")
    data = item.to_dict()
    data["visibility_scope"] = []
    with pytest.raises(SchemaValidationError):
        PlanItemV4.from_dict(data)


def test_evidence_state_enum():
    item = _base_item(family_id="fam:1", family_role="standalone")
    data = item.to_dict()
    data["evidence_state"] = "definitely_true"
    with pytest.raises(SchemaValidationError):
        PlanItemV4.from_dict(data)


def test_family_role_enum():
    item = _base_item(family_id="fam:1", family_role="standalone")
    data = item.to_dict()
    data["family_role"] = "sibling"
    with pytest.raises(SchemaValidationError):
        PlanItemV4.from_dict(data)
