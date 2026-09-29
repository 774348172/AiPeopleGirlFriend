"""Training rendering and exact current-assistant supervision for V5."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_gen_v4.adapters.modes.training import TrainingRecordV4, estimate_token_count

from .contracts import validate_teacher_target
from .scenario import validate_scenario

IGNORE_INDEX = -100
RUNTIME_GROUNDED_RENDER_PROFILE = "runtime-grounded-reply-v1"
GAME_REPLY_CONTEXT_RENDER_PROFILE = "GAME_REPLY_CONTEXT_V1"

# The runtime owns the canonical renderer.  The generator imports that
# implementation rather than maintaining a second, subtly different prompt
# formatter.  AiPeopleCreate remains independently runnable from its own root.
_AI_ROOT = Path(__file__).resolve().parents[3] / "AiPeople"
if str(_AI_ROOT) not in sys.path:
    sys.path.insert(0, str(_AI_ROOT))
from runtime.world_mind.game_reply_context import (  # noqa: E402
    build_game_reply_chat_messages,
    parse_game_reply_dialogue_lines,
    partition_game_reply_dialogue,
    render_game_reply_context_v1,
)


def canonical_model_view_text(model_view: dict[str, Any]) -> str:
    return json.dumps(
        model_view,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def render_runtime_grounded_training_record(
    scenario: dict[str, Any],
    teacher_target: dict[str, Any],
    *,
    system_anchor: str,
    sample_id: str | None = None,
    protocol_snapshot_id: str = "runtime-grounded-reply-v1",
) -> TrainingRecordV4:
    """Render one scenario as context plus exactly one supervised reply."""

    validate_scenario(scenario)
    validate_teacher_target(teacher_target)
    if not isinstance(system_anchor, str) or not system_anchor.strip():
        raise ValueError("runtime-grounded training requires a non-empty system anchor")
    messages = [
        {
            "role": "human",
            "content": canonical_model_view_text(scenario["model_view"]),
        },
        {"role": "assistant", "content": teacher_target["reply"].strip()},
    ]
    supervised = [1]
    return TrainingRecordV4(
        sample_id=sample_id or scenario["scenario_id"],
        mode="RUNTIME_GROUNDED_REPLY",
        render_profile_id=RUNTIME_GROUNDED_RENDER_PROFILE,
        messages=messages,
        supervised_message_indexes=supervised,
        supervised_token_count=estimate_token_count(messages, supervised),
        protocol_snapshot_id=protocol_snapshot_id,
        system_anchor=system_anchor.strip(),
    )


def render_game_reply_context_from_model_view(model_view: dict[str, Any]) -> str:
    """Render a generator ScenarioSpec with the production GAME_REPLY renderer.

    ``model_view`` is deliberately treated as an input projection, never as
    oracle data.  This adapter only translates the persisted projection into
    the renderer's typed arguments; it does not infer or resolve facts.
    """

    if not isinstance(model_view, dict):
        raise TypeError("model_view must be an object")
    snapshot = model_view.get("snapshot") or {}
    scene = snapshot.get("scene") or {}
    protagonist = snapshot.get("protagonist") or {}
    runtime = model_view.get("previous_heroine_runtime") or {}
    living = runtime.get("living_mind") or {}
    relationship = runtime.get("relationship") or {}
    frame = model_view.get("selected_memory_frame") or {}

    memories: list[str] = []
    for memory in frame.get("selected_memories") or ():
        if isinstance(memory, dict):
            content = memory.get("content")
            if isinstance(content, list):
                statement = "；".join(
                    f"{item.get('speaker', '未知说话人')}（{item.get('time', '未知时间')}；来源：{item.get('source', '未知来源')}）：{item.get('text', '')}"
                    for item in content if isinstance(item, dict) and str(item.get('text', '')).strip()
                )
            else:
                statement = str(memory.get("statement") or content or "").strip()
            if not statement:
                continue
            suffix: list[str] = []
            if memory.get("subject_display_name"):
                suffix.append(f"主体：{memory['subject_display_name']}")
            if memory.get("temporal_relation"):
                suffix.append(f"时间关系：{memory['temporal_relation']}")
            if memory.get("epistemic_modality"):
                suffix.append(f"证据状态：{memory['epistemic_modality']}")
            suffix.append("来源：已选择记忆")
            memories.append(f"{statement}（{'；'.join(suffix)}）" if suffix else statement)

    reports = [
        _report_text(value)
        for value in (model_view.get("player_fact_reports") or ())
        if isinstance(value, dict)
    ]
    pending = [
        _pending_text(value)
        for value in (model_view.get("pending_actions") or ())
        if isinstance(value, dict)
    ]
    dialogue_facts = [
        str(value).strip()
        for value in (model_view.get("game_feedback") or ())
        if str(value).strip()
        and not (
            model_view.get("player_fact_reports")
            and str(value).strip().startswith("权威世界状态：")
        )
    ]
    # The explicit authoritative_world projection is persisted for provenance;
    # its player-visible facts are copied into game_feedback by the scenario
    # builder so production and training use the identical renderer input.
    authoritative_unknown = list(model_view.get("unknown_facts") or ())
    approved = [
        str(value.get("description", "")).strip()
        for value in (model_view.get("approved_heroine_actions") or ())
        if isinstance(value, dict) and str(value.get("description", "")).strip()
    ]
    character_display_name = str(model_view.get("character_display_name") or "白未晞")
    dialogue_turns = parse_game_reply_dialogue_lines(
        tuple(str(value) for value in (model_view.get("recent_dialogue") or ()))
    )
    _, third_party_dialogue_evidence = partition_game_reply_dialogue(
        dialogue_turns,
        character_display_name=character_display_name,
    )
    return render_game_reply_context_v1(
        character_display_name=character_display_name,
        game_time=(datetime.fromisoformat(snapshot["captured_game_time"])
                   if snapshot.get("captured_game_time") else "未知时间"),
        location=str(scene.get("location_label") or "未知地点"),
        scene_text=str((snapshot.get("world_state") or {}).get("scene_text") or ""),
        protagonist_location=str(protagonist.get("location_label") or "未知地点"),
        protagonist_activity=str(protagonist.get("activity") or ""),
        protagonist_body=_body_text(protagonist.get("body_state") or {}) or "无额外身体状态",
        heroine={
            "form": living.get("form", ""),
            "body": living.get("body", ""),
            "emotion": living.get("emotion", ""),
            "attention": living.get("attention", ""),
            "current_activity": living.get("current_activity", ""),
            "immediate_intent": living.get("immediate_intent", ""),
        },
        relationship={
            "stage": relationship.get("stage", ""),
            "trust": relationship.get("trust", ""),
            "unresolved_tension": relationship.get("unresolved_tension", ""),
        },
        # Null states are unknown, never a current fact with the literal None.
        item_states={key: value for key, value in (scene.get("item_states") or {}).items() if value is not None},
        unknown_facts=[
            _unknown_text(value)
            for value in authoritative_unknown
            if isinstance(value, dict)
        ] + [f"{key}：未知（来源：游戏状态；原因：未登记可靠值）"
             for key, value in (scene.get("item_states") or {}).items() if value is None],
        player_fact_reports=reports,
        memories=memories,
        third_party_dialogue_evidence=third_party_dialogue_evidence,
        pending_actions=pending,
        game_feedback=dialogue_facts,
        approved_actions=approved,
    )


def render_game_reply_training_record(
    scenario: dict[str, Any],
    teacher_target: dict[str, Any],
    *,
    system_anchor: str,
    sample_id: str | None = None,
    protocol_snapshot_id: str = GAME_REPLY_CONTEXT_RENDER_PROFILE,
) -> TrainingRecordV4:
    """Build training messages that mirror production GAME_REPLY messages."""

    validate_scenario(scenario)
    validate_teacher_target(teacher_target)
    if not isinstance(system_anchor, str) or not system_anchor.strip():
        raise ValueError("GAME_REPLY_CONTEXT_V1 requires a non-empty system anchor")
    view = scenario["model_view"]
    if view.get("mode") != "GAME_REPLY":
        raise ValueError(
            "GAME_REPLY_CONTEXT_V1 training requires model_view.mode=GAME_REPLY; "
            "JUDGE_TURN scenarios belong to the independent backend branch"
        )
    context = render_game_reply_context_from_model_view(view)
    messages: list[dict[str, str]] = []
    dialogue_turns = parse_game_reply_dialogue_lines(
        tuple(str(value) for value in (view.get("recent_dialogue") or ()))
    )
    dialogue_messages = build_game_reply_chat_messages(
        dialogue_turns,
        character_display_name=str(view.get("character_display_name") or "白未晞"),
        current_protagonist_utterance=str(view["current_protagonist_utterance"]),
    )
    messages.extend(
        {"role": "human" if role == "user" else role, "content": content}
        for role, content in dialogue_messages
    )
    messages.append({"role": "assistant", "content": teacher_target["reply"].strip()})
    return TrainingRecordV4(
        sample_id=sample_id or scenario["scenario_id"],
        mode="GAME_REPLY",
        render_profile_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        messages=messages,
        supervised_message_indexes=[len(messages) - 1],
        supervised_token_count=estimate_token_count(messages, [len(messages) - 1]),
        protocol_snapshot_id=protocol_snapshot_id,
        system_anchor=f"{system_anchor.strip()}\n\n{context}",
    )


def _body_text(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value or "")
    return "；".join(f"{key}：{item}" for key, item in value.items())


def _pending_text(value: dict[str, Any]) -> str:
    description = str(value.get("description") or value.get("action_id") or "未命名动作")
    remaining = value.get("remaining_seconds")
    return f"{description}（剩余{remaining}秒）" if remaining is not None else description


def _report_text(value: dict[str, Any]) -> str:
    subject = str(value.get("subject") or "未知主体")
    predicate = str(value.get("predicate") or "事实")
    reported = str(value.get("value") or "")
    source = str(value.get("reported_by") or "protagonist")
    raw_status = str(value.get("status") or "pending_confirmation")
    status = _STATUS_LABELS.get(raw_status, raw_status)
    return f"{subject}的{predicate}：{reported}（来源：{source}；状态：{status}）"


_STATUS_LABELS = {
    "current": "当前有效",
    "unknown": "未知",
    "pending_confirmation": "待确认",
    "stale": "已过期",
    "false": "已否定",
}


def _unknown_text(value: dict[str, Any]) -> str:
    subject = str(value.get("subject") or "未知主体")
    predicate = str(value.get("predicate") or "事实")
    source = str(value.get("source") or "无可靠来源")
    reason = str(value.get("reason") or "没有直接证据")
    return f"{subject}的{predicate}：未知（来源：{source}；原因：{reason}）"


@dataclass(frozen=True, slots=True)
class CurrentAssistantSpan:
    message_index: int
    start: int
    end: int
    token_count: int
    special_token_count: int


def build_current_assistant_example(
    tokenizer: Any,
    record: TrainingRecordV4,
    *,
    max_seq_length: int,
    row_index: int = 0,
) -> tuple[dict[str, list[int]], CurrentAssistantSpan]:
    """Build labels for only the final assistant, failing on any truncation.

    The tokenizer's inference chat template is used for both the complete row
    and the generation prefix. Prefix mismatches fail closed.
    """

    if not record.system_anchor:
        raise ValueError(f"Row {row_index} requires a system anchor")
    if record.supervised_message_indexes != [len(record.messages) - 1]:
        raise ValueError(f"Row {row_index} must supervise only its final message")
    if not record.messages or record.messages[-1].get("role") != "assistant":
        raise ValueError(f"Row {row_index} must end with an assistant response")

    messages = [{"role": "system", "content": record.system_anchor}]
    messages.extend(
        {
            "role": "user" if message["role"] == "human" else message["role"],
            "content": message["content"],
        }
        for message in record.messages
    )
    target_index = len(messages) - 1
    full_text = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False
    )
    prefix_text = tokenizer.apply_chat_template(
        messages[:target_index], tokenize=False, add_generation_prompt=True
    )
    if not full_text.startswith(prefix_text):
        raise ValueError(
            f"Text prefix mismatch at row {row_index}; training and inference templates differ"
        )

    full = tokenizer(full_text, add_special_tokens=False, truncation=False)
    prefix = tokenizer(prefix_text, add_special_tokens=False, truncation=False)
    input_ids = list(full["input_ids"])
    prefix_ids = list(prefix["input_ids"])
    if len(input_ids) > max_seq_length:
        raise ValueError(
            f"Row {row_index} has {len(input_ids)} tokens, above {max_seq_length}; "
            "truncation would invalidate current-assistant supervision"
        )
    if input_ids[: len(prefix_ids)] != prefix_ids:
        raise ValueError(f"Token prefix mismatch at row {row_index}")
    start, end = len(prefix_ids), len(input_ids)
    if start >= end:
        raise ValueError(f"Row {row_index} assistant target has no tokens")

    content_ids = list(
        tokenizer(
            record.messages[-1]["content"],
            add_special_tokens=False,
            truncation=False,
        )["input_ids"]
    )
    target_ids = input_ids[start:end]
    content_found = bool(content_ids) and any(
        target_ids[offset : offset + len(content_ids)] == content_ids
        for offset in range(len(target_ids) - len(content_ids) + 1)
    )
    if not content_found:
        raise ValueError(f"Row {row_index} assistant content is outside its target span")

    labels = [IGNORE_INDEX] * len(input_ids)
    labels[start:end] = input_ids[start:end]
    special_ids = set(getattr(tokenizer, "all_special_ids", []))
    special_count = sum(token in special_ids for token in input_ids[start:end])
    if special_ids and special_count == 0:
        raise ValueError(f"Row {row_index} target lacks a supervised turn-closing token")
    if any(label != IGNORE_INDEX for label in labels[:start]):
        raise RuntimeError(f"Row {row_index} prompt tokens entered the loss mask")
    if labels[start:end] != input_ids[start:end]:
        raise RuntimeError(f"Row {row_index} target labels differ from input tokens")

    span = CurrentAssistantSpan(
        message_index=len(record.messages) - 1,
        start=start,
        end=end,
        token_count=end - start,
        special_token_count=special_count,
    )
    return (
        {
            "input_ids": input_ids,
            "attention_mask": list(full["attention_mask"]),
            "labels": labels,
        },
        span,
    )
