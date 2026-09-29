"""Static contract checks for the shared out-of-home scene canon.

These checks intentionally do not invoke a model.  They protect the boundary
between facts fixed by the canon and facts that must be supplied by a runtime
snapshot before the scene bundle is admitted to the generator.
"""
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CANON = ROOT.parent / "世界设定" / "松江府" / "外出场景正典.yaml"


def _bundle():
    value = yaml.safe_load(CANON.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_bundle_and_scene_ids_are_well_formed():
    bundle = _bundle()
    assert bundle["schema_version"] == "aip.shared_world_scene_canon_bundle.v1"
    scenes = bundle["scenes"]
    assert len(scenes) == 4
    ids = [scene["scene_id"] for scene in scenes]
    assert len(ids) == len(set(ids))
    assert {scene["scene_kind"] for scene in scenes} == {
        "public_cafe",
        "public_convenience_store",
        "outdoor_moving_route",
        "protagonist_owned_cafe",
    }


def test_each_scene_declares_runtime_and_unknown_boundaries():
    required = {
        "scene_id",
        "scene_kind",
        "location",
        "layout",
        "fixed_fixtures",
        "variable_items",
        "runtime_only_facts",
        "unknown_by_default",
    }
    for scene in _bundle()["scenes"]:
        assert required <= scene.keys(), scene["scene_id"]
        runtime = set(scene["runtime_only_facts"])
        unknown = set(scene["unknown_by_default"])
        # Runtime categories must have an explicit fail-closed unknown list.
        # Permanent facts are declared once in shared_rules for this bundle;
        # scene-local authority_rules is optional for scene-specific guards.
        assert runtime, scene["scene_id"]
        assert unknown, scene["scene_id"]


def test_confirmed_business_anchors_are_not_runtime_inferences():
    scenes = {scene["scene_id"]: scene for scene in _bundle()["scenes"]}
    cafe = scenes["songjiangfu.neighborhood_cafe"]
    store = scenes["songjiangfu.neighborhood_convenience_store"]
    walk = scenes["songjiangfu.shopping_walk"]
    own = scenes["songjiangfu.protagonist_cafe"]

    assert cafe["location"]["distance_from_home"] == {"max": 1, "unit": "公里"}
    assert store["location"]["distance_from_home"] == {"max": 1, "unit": "公里"}
    assert cafe["business"]["opening_hours"] == "11:00-20:00"
    assert store["business"]["opening_hours"] == "08:00-20:00"
    assert walk["location"]["distance_from_home"] == {"min": 3, "max": 4, "unit": "公里"}
    assert own["display_name"] == "心愿咖啡馆"
    assert own["ownership"]["staff"] == "none"
    assert own["business"]["opening_hours"] == "11:00-20:00"

    # Fixed opening hours/ownership never establish the live state.
    for scene in (cafe, store, own):
        unknown = "、".join(scene["unknown_by_default"])
        assert "是否营业" in unknown
    assert "是否到达" in "、".join(walk["runtime_only_facts"])


def test_cross_scene_transitions_require_structured_state():
    continuity = _bundle()["cross_scene_continuity"]
    assert continuity["allowed"] is True
    requirement = continuity["requirement"]
    assert "结构化事件" in requirement
    assert "时间" in requirement
    assert "当前地点" in requirement
