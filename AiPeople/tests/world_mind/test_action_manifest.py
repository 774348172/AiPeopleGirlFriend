from __future__ import annotations

import pytest

from runtime.world_mind.action_manifest import (
    ACTIONS,
    action_ids,
    get_action,
    is_valid_action_id,
    render_action_list,
)
from runtime.world_mind.model_gateway import HeroineDiegeticAction
from runtime.world_mind.real_model_gateway import (
    WorldMindModelOutputError,
    _actions_from_value,
)
from runtime.world_mind.state import (
    ActiveSceneState,
    HeroineRuntime,
    LivingMind,
    ModelReadableWorldState,
    ProtagonistLiveState,
    RelationshipState,
    TurnWorldSnapshot,
)
from tests.world_mind._helpers import INITIAL_GAME_TIME, session


def test_p0_action_whitelist() -> None:
    assert set(action_ids()) == {"cook_meal", "move_to", "pick_up_item"}
    assert is_valid_action_id("cook_meal")
    assert not is_valid_action_id("fly_to_moon")
    assert get_action("cook_meal") is not None
    assert get_action("nope") is None
    assert get_action("cook_meal").duration_game_seconds == 1200
    assert ACTIONS["cook_meal"].effects == {"item_states": {"桌上": "一碗热汤面"}}


def test_render_action_list_contains_descriptions() -> None:
    text = render_action_list()
    assert "cook_meal" in text
    assert "走到厨房做饭" in text
    assert "20 分钟" in text
    assert "move_to" in text
    assert "pick_up_item" in text


def test_diegetic_action_action_id_validation() -> None:
    # 旧格式（无 action_id）向后兼容
    old = HeroineDiegeticAction(description="我去做饭", evidence_refs=("ev-1",))
    assert old.action_id is None
    # 合法 action_id
    valid = HeroineDiegeticAction(
        description="我去做饭", evidence_refs=("ev-1",), action_id="cook_meal"
    )
    assert valid.action_id == "cook_meal"
    # 非法 action_id 拒绝
    with pytest.raises(ValueError, match="whitelisted"):
        HeroineDiegeticAction(
            description="点外卖", evidence_refs=("ev-1",), action_id="order_food"
        )


def test_actions_from_value_parses_optional_action_id() -> None:
    # 旧格式（无 action_id）解析为 None
    legacy = _actions_from_value(
        [{"description": "我去做饭", "evidence_refs": ["ev-1"]}]
    )
    assert len(legacy) == 1
    assert legacy[0].action_id is None
    # 新格式（含合法 action_id）
    parsed = _actions_from_value(
        [{"action_id": "cook_meal", "description": "我去做饭", "evidence_refs": ["ev-1"]}]
    )
    assert parsed[0].action_id == "cook_meal"
    # 非法 action_id 拒绝
    with pytest.raises(WorldMindModelOutputError, match="whitelisted"):
        _actions_from_value(
            [{"action_id": "order_food", "description": "点外卖", "evidence_refs": ["ev-1"]}]
        )
    # 未知字段拒绝
    with pytest.raises(WorldMindModelOutputError, match="do not match schema"):
        _actions_from_value(
            [{"action_id": "cook_meal", "description": "x", "evidence_refs": [], "extra": 1}]
        )


def _minimal_snapshot(**overrides: object) -> TurnWorldSnapshot:
    session_id = session()
    protagonist = ProtagonistLiveState(
        protagonist_id="protagonist",
        location_id="apartment_table",
        location_label="出租屋餐桌旁",
        activity="坐着休息",
        body_state={"当前状态": "有些疲惫"},
    )
    scene = ActiveSceneState(
        scene_id="apartment_table",
        location_label="出租屋餐桌旁",
        present_character_ids=("protagonist", "baiweixi"),
        item_states={"场景": "窗外下着雨"},
    )
    living_mind = LivingMind(
        form="人形",
        body="有些疲惫",
        emotion="平静",
        attention="注意着男主",
        current_activity="坐着休息",
        immediate_intent="陪男主聊天",
    )
    heroine = HeroineRuntime(
        save_id=session_id.save_id,
        world_id=session_id.world_id,
        character_id="baiweixi",
        version=1,
        living_mind=living_mind,
        relationship=RelationshipState(
            protagonist_id="protagonist",
            stage="初识",
            trust="低",
            unresolved_tension="无",
        ),
        evidence_refs=("seed",),
    )
    return TurnWorldSnapshot(
        snapshot_id="snapshot-1",
        request_id="request-1",
        session=session_id,
        captured_game_time=INITIAL_GAME_TIME,
        live_world_version=1,
        mind_state_version=1,
        world_state=ModelReadableWorldState(
            live_world_version=1,
            scene_text="窗外下着雨",
            protagonist_text="男主坐着休息",
        ),
        protagonist=protagonist,
        scene=scene,
        heroine_runtime=heroine,
        protagonist_utterance="我饿了",
        **overrides,
    )


def test_snapshot_pending_fields_default_and_validation() -> None:
    snapshot = _minimal_snapshot()
    assert snapshot.pending_actions == ()

    filled = _minimal_snapshot(
        pending_actions=({"action_id": "cook_meal", "remaining_seconds": 1000},),
    )
    assert filled.pending_actions[0]["action_id"] == "cook_meal"

    with pytest.raises(TypeError, match="pending_actions"):
        _minimal_snapshot(pending_actions=("not-a-dict",))
