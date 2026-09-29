from __future__ import annotations

import copy
from pathlib import Path

import yaml

from data_gen_v4.runtime_grounded import (
    render_game_reply_context_from_model_view,
    render_game_reply_training_record,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "runtime_grounded_golden_v1.yaml"


def test_null_item_state_is_explicit_unknown_not_a_current_none_fact():
    view = copy.deepcopy(yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"][0]["model_view"])
    view["snapshot"]["scene"]["item_states"] = {"微波炉.运行状态": None, "杯子.当前位置": "餐桌"}
    before = copy.deepcopy(view)
    text = render_game_reply_context_from_model_view(view)
    current = text.split("[当前权威事实]", 1)[1].split("[明确未知]", 1)[0]
    assert "微波炉.运行状态" not in current
    assert "微波炉.运行状态：未知" in text
    assert "None" not in text
    assert "杯子.当前位置：餐桌" in current
    assert view == before


def test_generator_uses_production_context_renderer_and_keeps_provenance() -> None:
    scenario = copy.deepcopy(
        yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"][0]
    )
    scenario["model_view"]["unknown_facts"] = [
        {
            "subject": "dentist_appointment",
            "predicate": "date",
            "source": "memory_selector",
            "reason": "没有可靠日期证据",
            "status": "unknown",
        }
    ]
    scenario["model_view"]["player_fact_reports"] = [
        {
            "subject": "预约",
            "predicate": "时间",
            "value": "星期六上午十点",
            "reported_by": "protagonist",
            "status": "pending_confirmation",
        }
    ]
    rendered = render_game_reply_context_from_model_view(scenario["model_view"])
    assert "[当前权威事实]" in rendered
    assert "[明确未知]" in rendered
    assert "未知（来源：memory_selector；原因：没有可靠日期证据）" in rendered
    assert "来源：protagonist；状态：待确认" in rendered
    assert rendered.index("[当前权威事实]") < rendered.index("[当前世界]")
    assert "oracle_view" not in rendered
    assert "required_assertions" not in rendered

    target = {
        "reply": "不是星期五，是星期六上午十点。",
        "declared_claims": [
            {
                "subject": "protagonist",
                "predicate": "dentist_appointment",
                "object": "星期六上午十点",
                "polarity": "positive",
                "temporal_status": "current",
                "source_fact_ids": ["fact.dentist.sat"],
            }
        ],
    }
    record = render_game_reply_training_record(
        scenario,
        target,
        system_anchor="你是白未晞。只输出说出口的话。",
    )
    assert record.mode == "GAME_REPLY"
    assert record.render_profile_id == "GAME_REPLY_CONTEXT_V1"
    assert record.messages[-1] == {"role": "assistant", "content": target["reply"]}
    assert record.supervised_message_indexes == [len(record.messages) - 1]
    assert "[当前权威事实]" in (record.system_anchor or "")


def test_generator_projects_third_party_dialogue_as_named_evidence() -> None:
    scenario = copy.deepcopy(
        yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"][0]
    )
    scenario["model_view"]["recent_dialogue"] = [
        "男主：你刚才听到了吗？",
        "程野：借书证在书桌收纳盒。",
        "白未晞：听到了。",
    ]
    rendered = render_game_reply_context_from_model_view(scenario["model_view"])
    assert "[第三方对话证据]" in rendered
    assert "说话人：程野；原话：借书证在书桌收纳盒。；来源：近期对话" in rendered

    target = {
        "reply": "是程野说的，他说借书证在书桌收纳盒。",
        "declared_claims": [
            {
                "subject": "library_card",
                "predicate": "location",
                "object": "书桌收纳盒",
                "polarity": "positive",
                "temporal_status": "current",
                "source_fact_ids": ["fact.library_card.location"],
            }
        ],
    }
    record = render_game_reply_training_record(
        scenario,
        target,
        system_anchor="你是白未晞。只输出说出口的话。",
    )
    assert "说话人：程野；原话：借书证在书桌收纳盒。；来源：近期对话" in (
        record.system_anchor or ""
    )
    assert record.messages == [
        {"role": "human", "content": "你刚才听到了吗？"},
        {"role": "assistant", "content": "听到了。"},
        {
            "role": "human",
            "content": scenario["model_view"]["current_protagonist_utterance"],
        },
        {"role": "assistant", "content": target["reply"]},
    ]
    assert record.supervised_message_indexes == [3]
