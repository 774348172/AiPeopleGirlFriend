from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "supervision.py"
)
SPEC = importlib.util.spec_from_file_location("gemma4_supervision", MODULE_PATH)
assert SPEC and SPEC.loader
supervision = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = supervision
SPEC.loader.exec_module(supervision)


class FakeGemmaTokenizer:
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
            values.extend(
                [self.TURN_START, self.ROLE_IDS["assistant"], self.NEWLINE]
            )
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


def test_all_assistant_spans_and_turn_end_tokens_are_supervised():
    tokenizer = FakeGemmaTokenizer()
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
        {"role": "assistant", "content": "A2"},
    ]

    example, spans = supervision.build_all_assistant_example(
        tokenizer, messages, max_seq_length=100, row_index=0
    )

    assert len(spans) == 2
    assert all(span.special_token_count == 1 for span in spans)
    for span in spans:
        assert example["input_ids"][span.start - 1] == tokenizer.NEWLINE
        assert example["labels"][span.start - 1] == supervision.IGNORE_INDEX
        assert example["input_ids"][span.end - 2] == tokenizer.TURN_END
        assert example["labels"][span.end - 2] == tokenizer.TURN_END
        assert example["labels"][span.start : span.end] == example["input_ids"][span.start : span.end]

    supervised_positions = {
        index
        for span in spans
        for index in range(span.start, span.end)
    }
    assert all(
        (label != supervision.IGNORE_INDEX) == (index in supervised_positions)
        for index, label in enumerate(example["labels"])
    )


def test_sequence_over_limit_fails_instead_of_truncating_targets():
    tokenizer = FakeGemmaTokenizer()
    messages = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "U"},
        {"role": "assistant", "content": "long answer"},
    ]

    with pytest.raises(ValueError, match="truncation would invalidate"):
        supervision.build_all_assistant_example(
            tokenizer, messages, max_seq_length=5, row_index=9
        )


def test_generation_prefix_mismatch_fails_closed():
    class BrokenTokenizer(FakeGemmaTokenizer):
        def apply_chat_template(self, messages, *, tokenize, add_generation_prompt):
            rendered = super().apply_chat_template(
                messages, tokenize=tokenize, add_generation_prompt=add_generation_prompt
            )
            return rendered + (",999" if add_generation_prompt else "")

    with pytest.raises(ValueError, match="Text prefix mismatch"):
        supervision.build_all_assistant_example(
            BrokenTokenizer(),
            [
                {"role": "system", "content": "S"},
                {"role": "user", "content": "U"},
                {"role": "assistant", "content": "A"},
            ],
            max_seq_length=100,
            row_index=3,
        )
