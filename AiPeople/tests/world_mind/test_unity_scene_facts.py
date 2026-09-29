import pytest
from pydantic import ValidationError
from tools.baiweixi_chat_app import WorldInput
from runtime.world_mind.compact_game_reply_context import decode_system_v2
from runtime.world_mind.compact_game_reply_context_v3 import decode_system_v3
from runtime.world_mind.model_payloads import prepare_v3_reply_system, V3_REPLY_CONTRACT
from runtime.world_mind.game_reply_context import render_game_reply_context_v1


def test_world_facts_replace_previous_values_preserve_names_and_allow_legacy():
    old = WorldInput(scene_facts=[{"label": "厨房门", "value": "已打开"}], held_item="一杯温水")
    new = WorldInput(scene_facts=[{"label": "厨房门", "value": "已关闭"}])
    assert old.item_states()["男主手持物"] == "一杯温水"
    assert new.item_states()["厨房门"] == "已关闭"
    assert new.item_states()["男主手持物"] == "手里没有拿物品"
    assert "厨房门" not in WorldInput().item_states()


def test_changed_and_removed_facts_explicitly_expire_old_state():
    current = WorldInput(scene_facts=[{"label": "厨房门", "value": "已打开"}])
    items = current.item_states({"厨房门": "已关闭", "浴室门": "已打开"})
    assert items["厨房门"] == "已打开"
    assert "旧记录「已关闭」已失效，当前「已打开」" in items["本轮游戏状态变化"]
    assert "浴室门：旧记录已失效，当前快照未提供此状态" in items["本轮游戏状态变化"]
    assert "浴室门" not in items
    assert "本轮游戏状态变化" not in current.item_states(items)


@pytest.mark.parametrize("facts", [
    [{"label": "厨房门", "value": "已打开"}, {"label": " 厨房门 ", "value": "已关闭"}],
    [{"label": "场景", "value": "覆盖"}],
    [{"label": "男主手持物", "value": "覆盖"}],
    [{"label": " ", "value": "未知"}],
    [{"label": "厨房门", "value": " "}],
    [{"label": str(i), "value": "已打开"} for i in range(11)],
])
def test_world_fact_payload_is_bounded_and_unambiguous(facts):
    with pytest.raises(ValidationError):
        WorldInput(scene_facts=facts)


def test_door_and_motion_facts_survive_production_v3_codec():
    world = WorldInput(scene="出租屋室内", scene_facts=[
        {"label": "厨房门", "value": "关闭受阻，尚未完成"},
        {"label": "阳台移窗", "value": "已打开"},
        {"label": "白未晞实际移动", "value": "正在行走，尚未到达目标"},
    ])
    system = render_game_reply_context_v1(
        character_display_name="白未晞", game_time="第1日 18:00:00", location="出租屋",
        scene_text=world.scene, protagonist_location=world.location_label,
        protagonist_activity=world.activity, protagonist_body=world.body,
        heroine={}, relationship={}, item_states=world.item_states())
    system = "[稳定角色知识；知道不等于主动透露]\n- 姓名：白未晞（可直接回答）\n" + system
    compact = prepare_v3_reply_system(system)
    assert decode_system_v2(decode_system_v3(compact.removesuffix(V3_REPLY_CONTRACT))) == system
