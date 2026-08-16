from __future__ import annotations

import json

from runtime._memory_propose import (
    MEMORY_PROPOSE_SYSTEM_PROMPT,
    build_memory_propose_messages,
    memory_propose_request_to_dict,
)

from ._helpers import EVAL_DIR, load_json, request


def test_prompt_is_background_only_and_contains_canonical_request() -> None:
    value = request()
    messages = build_memory_propose_messages(value)
    assert tuple(item["role"] for item in messages) == ("system", "user")
    assert json.loads(messages[1]["content"]) == memory_propose_request_to_dict(value)
    assert "不是在扮演秦未晞" in MEMORY_PROPOSE_SYSTEM_PROMPT
    assert "可以输出零条" in MEMORY_PROPOSE_SYSTEM_PROMPT
    assert "禁止 Markdown" in MEMORY_PROPOSE_SYSTEM_PROMPT
    assert "提醒" in MEMORY_PROPOSE_SYSTEM_PROMPT


def test_mode_profile_freezes_sampling_structure_and_scheduling() -> None:
    profile = load_json(EVAL_DIR / "mem02_mode_profile_v1.json")
    assert profile["mode"] == "MEMORY_PROPOSE"
    assert profile["model"] == {
        "family": "Qwen3.5-4B",
        "role": "shared_main_model",
        "stream": False,
        "thinking": False,
    }
    assert profile["sampling"] == {
        "max_output_tokens": 2048,
        "temperature": 0.1,
        "top_p": 0.8,
    }
    assert profile["structured_output"]["allow_empty_proposals"] is True
    assert profile["structured_output"]["allow_visible_text"] is False
    assert profile["scheduling"]["blocks_reply"] is False
    assert profile["scheduling"]["preemptible_by_player_turn"] is True
    assert profile["failure_policy"]["keyword_fallback"] is False
    assert profile["failure_policy"]["partial_acceptance"] is False
