"""Training rendering and exact current-assistant supervision for V5."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from data_gen_v4.adapters.modes.training import TrainingRecordV4, estimate_token_count

from .contracts import validate_teacher_target
from .scenario import validate_scenario

IGNORE_INDEX = -100
RUNTIME_GROUNDED_RENDER_PROFILE = "runtime-grounded-reply-v1"


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
