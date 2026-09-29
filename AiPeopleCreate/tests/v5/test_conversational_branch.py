from __future__ import annotations

import copy
from pathlib import Path

import yaml
import pytest

from data_gen_v4.runtime_grounded import (
    evaluate_conversational_candidate,
    render_conversational_training_record,
    validate_conversational_scenario,
)

FIXTURE = Path(__file__).parent / "fixtures" / "runtime_grounded_golden_v1.yaml"


def _scenario() -> dict:
    base = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))["scenarios"][0]
    base = copy.deepcopy(base)
    base["schema_version"] = "aip.conversational_reply_scenario.v1"
    base["task_type"] = "idle_chat"
    base["style_contract"] = {
        "style_intent": "轻松闲聊并自然承接男主",
        "reply_shape": "回应后可追问",
        "forbidden_styles": ["action_narration", "markdown", "assistant_tone"],
        "unsupported_expansion_policy": "reject",
    }
    if base["task_type"] == "natural_fact_embed":
        base["fact_contract"] = {"required_phrases": ["星期六上午十点"]}
    base["split_anchors"] = ["style:idle_chat", "scene:home", "shape:follow_up"]
    return base


def _audit() -> dict:
    return {
        "verdicts": {"voice": True, "turn_relevance": True, "naturalness": True, "unsupported_expansion": True},
        "decision": "approve",
        "reasons": [],
    }


def test_conversational_branch_uses_game_reply_renderer_and_current_assistant_only() -> None:
    scenario = _scenario()
    validate_conversational_scenario(scenario)
    record = render_conversational_training_record(
        scenario,
        {"reply": "今天还算清闲，你呢？"},
        system_anchor="你是白未晞。只输出说出口的话。",
    )
    assert record.mode == "CONVERSATIONAL_REPLY"
    assert record.render_profile_id == "GAME_REPLY_CONTEXT_V1"
    assert record.supervised_message_indexes == [len(record.messages) - 1]
    assert record.messages[-1]["content"] == "今天还算清闲，你呢？"


def test_conversational_gate_rejects_action_narration() -> None:
    result = evaluate_conversational_candidate(
        _scenario(),
        {"reply": "（歪了歪头）今天还算清闲。"},
        _audit(),
    )
    assert result["approved"] is False
    assert "action_narration" in result["reasons"]


def test_conversational_gate_accepts_style_audited_reply() -> None:
    result = evaluate_conversational_candidate(
        _scenario(),
        {"reply": "今天还算清闲，你呢？"},
        _audit(),
    )
    assert result["approved"] is True
