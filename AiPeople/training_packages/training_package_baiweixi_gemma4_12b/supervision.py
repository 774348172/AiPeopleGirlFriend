from __future__ import annotations

from dataclasses import dataclass
from typing import Any


IGNORE_INDEX = -100


@dataclass(frozen=True)
class AssistantSpan:
    message_index: int
    assistant_ordinal: int
    start: int
    end: int
    token_count: int
    special_token_count: int


def render_chat(tokenizer: Any, messages: list[dict[str, str]], generation_prompt: bool) -> str:
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=generation_prompt,
    )


def build_all_assistant_example(
    tokenizer: Any,
    messages: list[dict[str, str]],
    *,
    max_seq_length: int,
    row_index: int,
) -> tuple[dict[str, list[int]], list[AssistantSpan]]:
    if not messages or messages[0]["role"] != "system":
        raise ValueError(f"Row {row_index} must start with a system message")
    if messages[-1]["role"] != "assistant":
        raise ValueError(f"Row {row_index} must end with an assistant response")

    full_text = render_chat(tokenizer, messages, False)
    full = tokenizer(full_text, add_special_tokens=False, truncation=False)
    input_ids = list(full["input_ids"])
    if len(input_ids) > max_seq_length:
        raise ValueError(
            f"Row {row_index} has {len(input_ids)} tokens, above {max_seq_length}; "
            "truncation would invalidate assistant supervision"
        )

    labels = [IGNORE_INDEX] * len(input_ids)
    special_ids = set(getattr(tokenizer, "all_special_ids", []))
    spans: list[AssistantSpan] = []
    previous_end = 0

    for message_index, message in enumerate(messages):
        if message["role"] != "assistant":
            continue

        assistant_ordinal = len(spans) + 1
        prefix_text = render_chat(tokenizer, messages[:message_index], True)
        through_text = render_chat(tokenizer, messages[: message_index + 1], False)
        if not full_text.startswith(prefix_text):
            raise ValueError(
                f"Text prefix mismatch at row {row_index}, assistant {assistant_ordinal}; "
                "the training chat template may differ from the inference template"
            )
        if not full_text.startswith(through_text):
            raise ValueError(
                f"Text end mismatch at row {row_index}, assistant {assistant_ordinal}"
            )
        prefix_ids = tokenizer(
            prefix_text, add_special_tokens=False, truncation=False
        )["input_ids"]
        through_ids = tokenizer(
            through_text, add_special_tokens=False, truncation=False
        )["input_ids"]
        if input_ids[: len(prefix_ids)] != list(prefix_ids):
            raise ValueError(
                f"Token prefix mismatch at row {row_index}, assistant {assistant_ordinal}"
            )
        if input_ids[: len(through_ids)] != list(through_ids):
            raise ValueError(
                f"Token end mismatch at row {row_index}, assistant {assistant_ordinal}"
            )

        start, end = len(prefix_ids), len(through_ids)
        if start >= end:
            raise ValueError(
                f"Assistant target has no tokens at row {row_index}, assistant {assistant_ordinal}"
            )
        if start < previous_end:
            raise ValueError(
                f"Assistant target spans overlap at row {row_index}, assistant {assistant_ordinal}"
            )

        content_ids = list(
            tokenizer(
                message["content"], add_special_tokens=False, truncation=False
            )["input_ids"]
        )
        target_ids = input_ids[start:end]
        content_found = any(
            target_ids[offset : offset + len(content_ids)] == content_ids
            for offset in range(len(target_ids) - len(content_ids) + 1)
        )
        if not content_ids or not content_found:
            raise ValueError(
                f"Assistant content tokens are outside the rendered target span at row {row_index}, "
                f"assistant {assistant_ordinal}"
            )

        labels[start:end] = input_ids[start:end]
        special_token_count = sum(token_id in special_ids for token_id in input_ids[start:end])
        if special_ids and special_token_count == 0:
            raise ValueError(
                f"Assistant target lacks a supervised turn-closing special token at row {row_index}, "
                f"assistant {assistant_ordinal}"
            )
        spans.append(
            AssistantSpan(
                message_index=message_index,
                assistant_ordinal=assistant_ordinal,
                start=start,
                end=end,
                token_count=end - start,
                special_token_count=special_token_count,
            )
        )
        previous_end = end

    if not spans:
        raise RuntimeError(f"Row {row_index} has no assistant messages")

    expected_supervised = [False] * len(input_ids)
    for span in spans:
        expected_supervised[span.start : span.end] = [True] * span.token_count
    for token_index, should_be_supervised in enumerate(expected_supervised):
        is_supervised = labels[token_index] != IGNORE_INDEX
        if is_supervised != should_be_supervised:
            raise RuntimeError(
                f"Per-token assistant mask mismatch at row {row_index}, token {token_index}"
            )
        if is_supervised and labels[token_index] != input_ids[token_index]:
            raise RuntimeError(
                f"Supervised label differs from input token at row {row_index}, token {token_index}"
            )

    return (
        {
            "input_ids": input_ids,
            "attention_mask": list(full["attention_mask"]),
            "labels": labels,
        },
        spans,
    )
