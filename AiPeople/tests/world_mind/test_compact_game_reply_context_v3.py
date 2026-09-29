import pytest

from AiPeople.runtime.world_mind.compact_game_reply_context_v3 import (
    BOUNDARY_ORIGINAL,
    decode_system_v3,
    encode_system_v3,
)


def test_v3_roundtrip_preserves_provenance_and_order():
    original = (
        "[明确未知]\n【未知字段；共同来源与原因】{\"来源\":\"当前快照\",\"原因\":\"未登记\"}\n"
        "- 微波炉的电源状态：未知\n- 微波炉的运行模式：未知\n- 微波炉的剩余时间：未知\n"
        "- 电视的频道：未知\n"
        "【未知字段；共同来源与原因】{\"来源\":\"旧快照\",\"原因\":\"未登记\"}\n"
        "- 微波炉的电源状态：未知\n- 微波炉的运行模式：未知\n- 微波炉的剩余时间：未知\n"
        "[当前世界]\n世界\n[回答依据约束]\n" + BOUNDARY_ORIGINAL + "\n"
    )
    compact = encode_system_v3(original)
    assert compact.count("【未知组V3】微波炉") == 2
    assert compact.count("【合并回答约束V3】") == 1
    assert decode_system_v3(compact) == original


def test_unmatched_boundary_and_ambiguous_fields_remain_unchanged():
    original = (
        "[明确未知]\n- 微波炉的电源、运行模式：未知\n"
        "- 微波炉的剩余时间：未知\n- 微波炉的内部食物：未知\n"
        "[当前世界]\n[回答依据约束]\n不要猜测。\n"
    )
    assert encode_system_v3(original) == original
    assert decode_system_v3(original) == original


def test_malformed_group_fails_closed():
    with pytest.raises(ValueError, match="Invalid V3 unknown"):
        decode_system_v3("[明确未知]\n【未知组V3】微波炉：电源\n[当前世界]\n")
