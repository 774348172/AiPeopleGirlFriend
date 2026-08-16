from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from runtime._selector_query import (
    MAX_RENDERED_QUERY_CHARS,
    RECENT_DIALOGUE_TURNS,
    SELECTOR_QUERY_SCHEMA_VERSION,
    SelectorQueryTooLongError,
    build_selector_query,
    canonical_selector_query_json,
    render_selector_query_text,
    selector_query_sha256,
    selector_query_to_dict,
)
from tests.selector_query._helpers import make_context


def test_query_serialization_hash_and_renderer_are_deterministic() -> None:
    context = make_context(history_texts=("第一句", "第二句"))
    first = build_selector_query(context)
    second = build_selector_query(context)
    assert first == second
    assert first.schema_version == SELECTOR_QUERY_SCHEMA_VERSION
    assert canonical_selector_query_json(first) == canonical_selector_query_json(second)
    assert selector_query_sha256(first) == hashlib.sha256(
        canonical_selector_query_json(first).encode("utf-8")
    ).hexdigest()
    assert json.loads(canonical_selector_query_json(first)) == selector_query_to_dict(first)
    assert render_selector_query_text(first) == render_selector_query_text(second)


@pytest.mark.parametrize("history_size", [0, 2, 4])
def test_recent_dialogue_preserves_zero_two_and_four_complete_turns(history_size: int) -> None:
    texts = tuple(f"完整消息 {index}" for index in range(history_size))
    query = build_selector_query(make_context(history_texts=texts))
    assert len(query.recent_dialogue) == history_size
    assert tuple(turn.text for turn in query.recent_dialogue) == texts


def test_recent_dialogue_keeps_latest_four_without_repeating_current_message() -> None:
    query = build_selector_query(
        make_context(
            current_text="当前消息",
            history_texts=("旧一", "旧二", "旧三", "旧四", "旧五", "旧六"),
        )
    )
    assert RECENT_DIALOGUE_TURNS == 4
    assert tuple(turn.text for turn in query.recent_dialogue) == (
        "旧三",
        "旧四",
        "旧五",
        "旧六",
    )
    assert all(turn.text != query.current_user_message for turn in query.recent_dialogue)


def test_whitespace_is_normalized_without_losing_unicode() -> None:
    query = build_selector_query(
        make_context(
            current_text="  我\n还记得　那家店  ",
            history_texts=("  秦未晞\t说：\n好。 ",),
        )
    )
    assert query.current_user_message == "我 还记得 那家店"
    assert query.recent_dialogue[0].text == "秦未晞 说： 好。"


def test_working_state_is_a_fixed_secret_free_whitelist() -> None:
    query = build_selector_query(make_context(history_texts=("问", "答")))
    payload = selector_query_to_dict(query)
    state = payload["compact_working_state"]
    assert state == {
        "has_completed_exchange": True,
        "unresolved_prior_user_turns": 1,
    }
    serialized = canonical_selector_query_json(query)
    assert "event_id" not in serialized
    assert "prior-unresolved" not in serialized
    assert "explicit_recall_cues" not in serialized
    assert "database" not in serialized


def test_time_context_uses_only_injected_time_and_timezone() -> None:
    instant = datetime(2026, 8, 8, 23, 59, 1, tzinfo=timezone(timedelta(hours=8)))
    query = build_selector_query(make_context(now=instant, timezone="Asia/Shanghai"))
    assert query.current_time_context.local_datetime == "2026-08-08T23:59:01+08:00"
    assert query.current_time_context.timezone == "Asia/Shanghai"
    assert query.current_time_context.weekday == 6
    assert query.current_time_context.part_of_day == "evening"


def test_character_budget_drops_only_complete_oldest_messages() -> None:
    query = build_selector_query(
        make_context(
            current_text="当前",
            history_texts=("甲" * 240, "乙" * 120, "丙" * 20, "丁" * 20),
        )
    )
    assert len(render_selector_query_text(query)) <= MAX_RENDERED_QUERY_CHARS
    assert tuple(turn.text for turn in query.recent_dialogue) == ("丙" * 20, "丁" * 20)


def test_overlong_current_message_fails_instead_of_truncating() -> None:
    with pytest.raises(SelectorQueryTooLongError, match="without history"):
        build_selector_query(make_context(current_text="长" * MAX_RENDERED_QUERY_CHARS))

