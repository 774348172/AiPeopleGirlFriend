"""Natural conversational reply generation for the V5 data pipeline.

This branch deliberately does not reuse the fact-assertion gates.  It still
uses the production GAME_REPLY renderer, while style and turn quality are
reviewed by a separate contract.
"""

from __future__ import annotations

import json
import re
from typing import Any

from data_gen_v4.adapters.modes.training import TrainingRecordV4, estimate_token_count

from .contracts import RuntimeGroundedContractError
from .renderer import (
    GAME_REPLY_CONTEXT_RENDER_PROFILE,
    build_game_reply_chat_messages,
    parse_game_reply_dialogue_lines,
    render_game_reply_context_from_model_view,
)

CONVERSATIONAL_MODE = "CONVERSATIONAL_REPLY"
STYLE_TASKS = (
    "idle_chat",
    "shared_activity",
    "emotion_attunement",
    "light_teasing",
    "proactive_care",
    "follow_up_question",
    "natural_fact_embed",
)

_ACTION_NARRATION = re.compile(
    r"(?:\([^\n()]{1,80}\)|（[^\n（）]{1,80}）|\*[^\n*]{1,80}\*)"
)
_INTERNAL_OUTPUT = re.compile(r"(?:^```|^\s*(?:JSON|assistant|系统)[:：])", re.I | re.M)
_UNAUTHORIZED_ACTION = re.compile(r"(?:我(?:已经|刚才|把|去)|我修好了|我检查过)")
_HEDGED_FACT = re.compile(r"(?:应该|可能|大概|估计|差不多)")


def validate_conversational_scenario(value: dict[str, Any]) -> None:
    errors: list[str] = []
    if value.get("schema_version") != "aip.conversational_reply_scenario.v1":
        errors.append("schema_version must be aip.conversational_reply_scenario.v1")
    if not isinstance(value.get("scenario_id"), str) or not value["scenario_id"].strip():
        errors.append("scenario_id is required")
    if value.get("task_type") not in STYLE_TASKS:
        errors.append(f"task_type must be one of {STYLE_TASKS}")
    view = value.get("model_view")
    if not isinstance(view, dict):
        errors.append("model_view is required")
    elif view.get("mode") != "GAME_REPLY":
        errors.append("conversational scenarios must use model_view.mode=GAME_REPLY")
    # Legacy fixtures may omit the new projection.  The generator entrypoints
    # populate it for every new sample; production payload migration can then
    # make it mandatory without invalidating historical fixtures.
    contract = value.get("style_contract")
    if not isinstance(contract, dict):
        errors.append("style_contract is required")
    else:
        for key in ("style_intent", "reply_shape", "forbidden_styles", "unsupported_expansion_policy"):
            if key not in contract:
                errors.append(f"style_contract.{key} is required")
    if value.get("task_type") == "natural_fact_embed":
        fact_contract = value.get("fact_contract")
        if not isinstance(fact_contract, dict) or not isinstance(fact_contract.get("required_phrases"), list) or not fact_contract["required_phrases"]:
            errors.append("natural_fact_embed requires fact_contract.required_phrases")
    anchors = value.get("split_anchors")
    if not isinstance(anchors, list) or len(anchors) < 3 or not all(isinstance(x, str) for x in anchors):
        errors.append("split_anchors must contain at least three strings")
    if errors:
        raise RuntimeGroundedContractError("conversational_scenario", errors)


def validate_conversational_target(value: dict[str, Any]) -> None:
    reply = value.get("reply") if isinstance(value, dict) else None
    if not isinstance(reply, str) or not reply.strip() or len(reply) > 400:
        raise RuntimeGroundedContractError("conversational_target", ["reply must be 1..400 characters"])
    if set(value) - {"reply"}:
        raise RuntimeGroundedContractError(
            "conversational_target", ["teacher target may contain only reply"]
        )
    if re.search(r'[{}]|JSON格式|重新生成|格式错误', reply, re.I):
        raise RuntimeGroundedContractError('conversational_target', ['reply contains serialization or generation-process text'])


def validate_conversational_audit(value: dict[str, Any]) -> None:
    required = {"verdicts", "decision", "reasons"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise RuntimeGroundedContractError("conversational_audit", ["missing audit fields"])
    verdicts = value.get("verdicts")
    if not isinstance(verdicts, dict) or not all(
        isinstance(verdicts.get(key), bool)
        for key in ("voice", "turn_relevance", "naturalness", "unsupported_expansion")
    ):
        raise RuntimeGroundedContractError("conversational_audit", ["invalid style verdicts"])
    if value.get("decision") not in {"approve", "reject", "human_review"}:
        raise RuntimeGroundedContractError("conversational_audit", ["invalid decision"])


def evaluate_conversational_candidate(
    scenario: dict[str, Any], target: dict[str, Any], audit: dict[str, Any]
) -> dict[str, Any]:
    validate_conversational_scenario(scenario)
    try:
        validate_conversational_target(target)
    except RuntimeGroundedContractError as error:
        return {"approved": False, "verdicts": {}, "reasons": ["invalid_or_empty_reply", *error.errors]}
    validate_conversational_audit(audit)
    reply = target["reply"].strip()
    reasons: list[str] = []
    if _ACTION_NARRATION.search(reply):
        reasons.append("action_narration")
    if _INTERNAL_OUTPUT.search(reply):
        reasons.append("internal_or_markdown_output")
    if scenario["task_type"] == "natural_fact_embed":
        required_phrases = scenario.get("fact_contract", {}).get("required_phrases", [])
        answer_required = scenario.get("fact_contract", {}).get("answer_required", True)
        if answer_required and not all(str(phrase) in reply for phrase in required_phrases):
            reasons.append("required_fact_phrase_missing")
        if _UNAUTHORIZED_ACTION.search(reply):
            reasons.append("unauthorized_action_or_causal_claim")
        if _HEDGED_FACT.search(reply):
            reasons.append("hedged_or_unsupported_fact_claim")
    verdicts = dict(audit["verdicts"])
    if reasons:
        verdicts["naturalness"] = False
    approved = not reasons and audit["decision"] == "approve" and all(verdicts.values())
    return {"approved": approved, "verdicts": verdicts, "reasons": reasons + list(audit.get("reasons") or [])}


def render_conversational_training_record(
    scenario: dict[str, Any], target: dict[str, Any], *, system_anchor: str, sample_id: str | None = None
) -> TrainingRecordV4:
    validate_conversational_scenario(scenario)
    validate_conversational_target(target)
    if not isinstance(system_anchor, str) or not system_anchor.strip():
        raise ValueError("CONVERSATIONAL_REPLY requires a non-empty system anchor")
    view = scenario["model_view"]
    context = render_game_reply_context_from_model_view(view)
    turns = parse_game_reply_dialogue_lines(tuple(str(x) for x in view.get("recent_dialogue") or ()))
    dialogue = build_game_reply_chat_messages(
        turns,
        character_display_name=str(view.get("character_display_name") or "白未晞"),
        current_protagonist_utterance=str(view["current_protagonist_utterance"]),
    )
    messages = [
        {"role": "human" if role == "user" else role, "content": content}
        for role, content in dialogue
    ]
    messages.append({"role": "assistant", "content": target["reply"].strip()})
    return TrainingRecordV4(
        sample_id=sample_id or scenario["scenario_id"],
        mode=CONVERSATIONAL_MODE,
        render_profile_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        messages=messages,
        supervised_message_indexes=[len(messages) - 1],
        supervised_token_count=estimate_token_count(messages, [len(messages) - 1]),
        protocol_snapshot_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        system_anchor=f"{system_anchor.strip()}\n\n{context}",
    )
