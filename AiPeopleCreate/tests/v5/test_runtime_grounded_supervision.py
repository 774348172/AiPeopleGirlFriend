from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data_gen_v4.adapters.modes.training import TrainingRecordV4
from data_gen_v4.runtime_grounded import (
    IGNORE_INDEX,
    build_current_assistant_example,
    render_runtime_grounded_training_record,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "runtime_grounded_golden_v1.yaml"


class FakeChatTokenizer:
    BOS = 1
    TURN_START = 2
    TURN_END = 3
    NEWLINE = 4
    ROLE_IDS = {"system": 10, "user": 11, "assistant": 12}
    all_special_ids = [BOS, TURN_START, TURN_END]

    def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
        assert tokenize is False
        values = [self.BOS]
        for message in messages:
            values.extend(
                [
                    self.TURN_START,
                    self.ROLE_IDS[message["role"]],
                    self.NEWLINE,
                    *(1000 + ord(char) for char in message["content"]),
                    self.TURN_END,
                    self.NEWLINE,
                ]
            )
        if add_generation_prompt:
            values.extend([self.TURN_START, self.ROLE_IDS["assistant"], self.NEWLINE])
        return ",".join(map(str, values))

    def __call__(self, text, *, add_special_tokens, truncation):
        assert add_special_tokens is False
        assert truncation is False
        values = (
            [int(value) for value in text.split(",")]
            if "," in text
            else [1000 + ord(char) for char in text]
        )
        return {"input_ids": values, "attention_mask": [1] * len(values)}


@pytest.fixture(scope="module")
def scenario() -> dict:
    return yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"][0]


def _target() -> dict:
    return {
        "reply": "不是星期五下午，已经改到星期六上午十点了。",
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


def test_renderer_contains_only_production_model_view_and_one_target(scenario: dict) -> None:
    record = render_runtime_grounded_training_record(
        scenario, _target(), system_anchor="你是白未晞。只输出说出口的话。"
    )
    assert record.mode == "RUNTIME_GROUNDED_REPLY"
    assert [message["role"] for message in record.messages] == ["human", "assistant"]
    assert record.supervised_message_indexes == [1]
    assert record.messages[1]["content"] == _target()["reply"]
    user_text = record.messages[0]["content"]
    assert "current_protagonist_utterance" in user_text
    assert "recent_dialogue" in user_text
    assert "oracle_view" not in user_text
    assert "required_assertions" not in user_text
    assert "available_actions" not in user_text


def test_per_token_mask_supervises_only_current_assistant(scenario: dict) -> None:
    record = render_runtime_grounded_training_record(
        scenario, _target(), system_anchor="你是白未晞。只输出说出口的话。"
    )
    example, span = build_current_assistant_example(
        FakeChatTokenizer(), record, max_seq_length=10000
    )
    assert span.message_index == 1
    assert span.special_token_count == 1
    assert all(label == IGNORE_INDEX for label in example["labels"][: span.start])
    assert example["labels"][span.start : span.end] == example["input_ids"][span.start : span.end]


def test_historical_assistant_remains_outside_loss_mask() -> None:
    record = TrainingRecordV4(
        sample_id="mask-history",
        mode="RUNTIME_GROUNDED_REPLY",
        render_profile_id="runtime-grounded-reply-v1",
        messages=[
            {"role": "human", "content": "旧问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "human", "content": "当前问题"},
            {"role": "assistant", "content": "当前回答"},
        ],
        supervised_message_indexes=[3],
        supervised_token_count=4,
        protocol_snapshot_id="runtime-grounded-reply-v1",
        system_anchor="系统锚",
    )
    example, span = build_current_assistant_example(
        FakeChatTokenizer(), record, max_seq_length=1000
    )
    old_answer_id = 1000 + ord("旧")
    old_positions = [i for i, token in enumerate(example["input_ids"]) if token == old_answer_id]
    assert old_positions
    assert all(example["labels"][index] == IGNORE_INDEX for index in old_positions)
    assert span.message_index == 3


def test_over_limit_rejects_instead_of_truncating(scenario: dict) -> None:
    record = render_runtime_grounded_training_record(
        scenario, _target(), system_anchor="系统锚"
    )
    with pytest.raises(ValueError, match="truncation would invalidate"):
        build_current_assistant_example(
            FakeChatTokenizer(), record, max_seq_length=5, row_index=9
        )


def test_chat_template_prefix_drift_is_rejected(scenario: dict) -> None:
    class BrokenTokenizer(FakeChatTokenizer):
        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            value = super().apply_chat_template(
                messages, tokenize=tokenize, add_generation_prompt=add_generation_prompt
            )
            return value + (",999" if add_generation_prompt else "")

    record = render_runtime_grounded_training_record(
        scenario, _target(), system_anchor="系统锚"
    )
    with pytest.raises(ValueError, match="Text prefix mismatch"):
        build_current_assistant_example(BrokenTokenizer(), record, max_seq_length=10000)

