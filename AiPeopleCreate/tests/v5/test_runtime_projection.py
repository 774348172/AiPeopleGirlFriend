from __future__ import annotations

import copy
import sys
from datetime import datetime
from pathlib import Path

import pytest

AIPEOPLE_ROOT = Path(__file__).resolve().parents[3] / "AiPeople"
sys.path.insert(0, str(AIPEOPLE_ROOT))

from runtime.world_mind.contracts import RuntimeSessionIdentity  # noqa: E402
from runtime.world_mind.model_payloads import judge_payload  # noqa: E402
from runtime.world_mind.state import (  # noqa: E402
    ActiveSceneState,
    HeroineRuntime,
    LivingMind,
    ModelReadableWorldState,
    ProtagonistLiveState,
    RelationshipState,
    TurnWorldSnapshot,
)

from data_gen_v4.runtime_grounded import (  # noqa: E402
    ProjectionContractError,
    project_judge_payload,
)


def _production_payload() -> dict:
    session = RuntimeSessionIdentity(
        save_id="save_v5",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id="conversation_v5",
    )
    heroine = HeroineRuntime(
        save_id=session.save_id,
        world_id=session.world_id,
        character_id=session.active_character_id,
        version=3,
        living_mind=LivingMind(
            form="人形",
            body="正常",
            emotion="平静",
            attention="男主的问题",
            current_activity="聊天",
            immediate_intent="回答问题",
        ),
        relationship=RelationshipState(
            protagonist_id="protagonist",
            stage="熟悉",
            trust="较高",
            unresolved_tension="暂无未解决矛盾",
        ),
        evidence_refs=("event-runtime",),
    )
    snapshot = TurnWorldSnapshot(
        snapshot_id="snapshot_v5",
        request_id="request_v5",
        session=session,
        captured_game_time=datetime(1, 10, 11, 18, 30),
        live_world_version=4,
        mind_state_version=heroine.version,
        world_state=ModelReadableWorldState(
            live_world_version=4,
            scene_text="两人在出租屋客厅。",
            protagonist_text="男主正在询问预约时间。",
        ),
        protagonist=ProtagonistLiveState(
            protagonist_id="protagonist",
            location_id="rented_home_living_room",
            location_label="出租屋客厅",
            activity="聊天",
            body_state={"fatigue": "轻微"},
        ),
        scene=ActiveSceneState(
            scene_id="rented_home_living_room",
            location_label="出租屋客厅",
            present_character_ids=("protagonist", "baiweixi"),
            item_states={},
        ),
        heroine_runtime=heroine,
        protagonist_utterance="所以是星期六上午十点，对吗？",
        protagonist_utterance_event_id="event_player_v5",
        game_feedback=("当前预约是星期六上午十点。",),
    )
    return judge_payload(snapshot, ("男主：原来是星期五。", "白未晞：我记得。"))


def test_projection_matches_real_production_payload_and_removes_only_actions() -> None:
    payload = _production_payload()
    original = copy.deepcopy(payload)
    projected = project_judge_payload(payload)
    assert payload == original
    assert projected == {key: value for key, value in payload.items() if key != "available_actions"}
    assert "available_actions" not in projected
    assert projected["snapshot"]["live_world_version"] == 4
    assert projected["selected_memory_frame"]["selected_memories"] == []
    assert projected["recent_dialogue"] == ["男主：原来是星期五。", "白未晞：我记得。"]
    assert projected["game_feedback"] == ["当前预约是星期六上午十点。"]


@pytest.mark.parametrize("mutation", ["missing", "unknown", "nested_drift"])
def test_projection_fails_closed_on_runtime_shape_drift(mutation: str) -> None:
    payload = _production_payload()
    if mutation == "missing":
        del payload["game_feedback"]
    elif mutation == "unknown":
        payload["new_runtime_field"] = "must be reviewed"
    else:
        payload["snapshot"]["new_nested_field"] = "must be reviewed"
    with pytest.raises(ProjectionContractError):
        project_judge_payload(payload)
