import pytest

from AiPeople.runtime.world_mind.compact_game_reply_context import (
    decode_system_v2,
    encode_system_v2,
)


def context(facts, unknown, profile="- name: 白未晞（可直接回答）\n"):
    return (
        "前言\n[稳定角色知识；知道不等于主动透露]\n" + profile
        + "[角色锚]\n角色\n[当前权威事实]\n" + facts
        + "[本轮玩家事实报告]\n无\n[明确未知]\n" + unknown
        + "[当前世界]\n现在\n"
    )


@pytest.mark.parametrize("facts,unknown", [
    (
        "- 店名：心愿（来源：公开经营资料）（当前有效，来源：游戏反馈）\n"
        "- 营业：是（来源：当前快照）（当前有效，来源：游戏反馈）\n"
        "- 声称；获知时间：仅为正文（来源：当前快照）；获知时间：昨天（当前有效，来源：游戏反馈）\n"
        "- 普通行\n",
        "- 洗衣机状态：未知（来源：当前快照未登记；原因：未登记可靠值）\n"
        "- 风扇状态：未知（来源：当前快照未登记；原因：未登记可靠值）\n"
        "- 自由说明\n",
    ),
    ("无已登记的当前权威事实\n", "无\n"),
])
def test_roundtrip(facts, unknown):
    original = context(facts, unknown, "- name: 白未晞（可直接回答）\n- limit: 不编造（可直接回答）\n")
    compact = encode_system_v2(original)
    assert decode_system_v2(compact) == original
    assert "【固定设定；共同来源】" in compact or facts.startswith("无")


def test_reserved_marker_fails_closed():
    with pytest.raises(ValueError, match="reserved"):
        encode_system_v2(context("无\n", "无\n") + "【原样】")


def test_invalid_compact_group_fails_closed():
    original = context("无\n", "无\n")
    with pytest.raises(ValueError, match="semantics"):
        decode_system_v2(original)
