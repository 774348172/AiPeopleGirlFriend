from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from runtime import RelationshipRuntime, RuntimeConfig, UserMessage
from runtime._ledger import EventLedger
from runtime._prompt import build_reply_messages
from runtime.adapters import FakeReplyModel


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)
YESTERDAY = NOW - timedelta(days=1)


def message(
    request_id: str,
    text: str,
    *,
    conversation_id: str = "c1",
    occurred_at: datetime = NOW,
) -> UserMessage:
    return UserMessage(
        request_id,
        conversation_id,
        text,
        occurred_at,
        "Asia/Shanghai",
    )


async def collect(runtime: RelationshipRuntime, value: UserMessage):
    return [event async for event in runtime.handle_turn(value)]


def seed_turns(config: RuntimeConfig, turns: list[tuple[UserMessage, str]]) -> None:
    ledger = EventLedger.open(config.database_path, clock=lambda: NOW)
    try:
        for user_message, reply in turns:
            result = ledger.append_user_message(user_message)
            ledger.append_character_message(
                request_id=user_message.request_id,
                conversation_id=user_message.conversation_id,
                user_event_id=result.event.event_id,
                text=reply,
            )
    finally:
        ledger.close()


def close_open_epoch(config: RuntimeConfig, conversation_id: str = "c1") -> None:
    connection = sqlite3.connect(config.database_path)
    try:
        last_sequence = connection.execute(
            "SELECT MAX(sequence_no) FROM events WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE conversation_epochs
            SET state = 'closed', closed_sequence_no = ?,
                close_reason = 'context_budget', closed_at = ?
            WHERE conversation_id = ? AND state = 'open'
            """,
            (last_sequence, NOW.isoformat(), conversation_id),
        )
        connection.commit()
    finally:
        connection.close()


async def recall_context(config: RuntimeConfig, text: str, request_id: str = "query"):
    model = FakeReplyModel(["收到"])
    async with RelationshipRuntime.open(config, model, clock=lambda: NOW) as runtime:
        await collect(runtime, message(request_id, text))
    return model.requests[0].context


@pytest.mark.parametrize(
    ("query", "expected_kind"),
    [
        ("你还记得旅行计划吗？", "explicit_phrase"),
        ("你还记得昨天那件事吗？", "explicit_time"),
        ("你还记得昨天的旅行计划吗？", "explicit_event"),
    ],
)
async def test_phrase_time_and_combined_cues_recall_old_epoch(
    tmp_path, query, expected_kind
) -> None:
    config = RuntimeConfig(tmp_path)
    seed_turns(
        config,
        [
            (
                message("old", "旅行计划定在周六去苏州", occurred_at=YESTERDAY),
                "好，我记住了。",
            )
        ],
    )
    close_open_epoch(config)

    context = await recall_context(config, query)

    assert context.recall_frame.uncertainty == "none"
    assert context.recall_frame.query_kind == expected_kind
    assert "旅行计划定在周六去苏州" in context.recall_frame.evidence[0].verbatim_excerpt
    assert context.current_user_event_id not in context.recall_frame.source_event_ids


async def test_normal_question_never_calls_recall_query(tmp_path, monkeypatch) -> None:
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["不知道实时天气。"])
    runtime = RelationshipRuntime.open(config, model, clock=lambda: NOW)

    def unexpected_query(**_kwargs):
        raise AssertionError("normal input must not query recall")

    monkeypatch.setattr(runtime._ledger, "find_recall_episodes", unexpected_query)
    async with runtime:
        await collect(runtime, message("normal", "今天天气怎么样"))

    assert model.requests[0].context.recall_frame.uncertainty == "no_query"


@pytest.mark.parametrize(
    "hostile_phrase",
    [
        '旅行计划" OR "秘密地点',
        "旅行计划 OR 秘密地点",
        "旅行计划\nOR 秘密地点",
        "旅行计划" + "很长" * 200,
    ],
)
async def test_fts_syntax_is_always_treated_as_one_bounded_literal(
    tmp_path, hostile_phrase
) -> None:
    config = RuntimeConfig(tmp_path)
    seed_turns(
        config,
        [(message("secret", "秘密地点在旧车站"), "知道了。")],
    )
    close_open_epoch(config)

    context = await recall_context(config, f"你还记得{hostile_phrase}吗？")

    assert context.recall_frame.uncertainty in {"no_match", "ambiguous"}
    assert context.recall_frame.evidence == ()


async def test_recall_is_conversation_scoped(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    seed_turns(
        config,
        [
            (message("c1-old", "普通旧事", conversation_id="c1"), "嗯。"),
            (
                message("c2-old", "旅行计划在海边", conversation_id="c2"),
                "好。",
            ),
        ],
    )
    close_open_epoch(config, "c1")
    close_open_epoch(config, "c2")

    context = await recall_context(config, "你还记得旅行计划吗？")

    assert context.recall_frame.uncertainty == "no_match"


async def test_anchor_restores_one_complete_message_on_each_side_in_order(
    tmp_path,
) -> None:
    config = RuntimeConfig(tmp_path)
    seed_turns(
        config,
        [
            (message("before", "前一句"), "目标旅行计划"),
            (message("after", "后一句"), "后一个回答"),
        ],
    )
    close_open_epoch(config)

    context = await recall_context(config, "你还记得目标旅行计划吗？")
    evidence = context.recall_frame.evidence[0]

    assert evidence.verbatim_excerpt.splitlines() == [
        "玩家：前一句",
        "秦未晞：目标旅行计划",
        "玩家：后一句",
    ]
    assert len(evidence.event_ids) == 3


async def test_equal_candidates_have_stable_sequence_order_and_no_duplicate_ids(
    tmp_path,
) -> None:
    config = RuntimeConfig(tmp_path, recall_excerpt_chars=80)
    seed_turns(
        config,
        [
            (message("first", "旅行计划甲"), "第一答"),
            (message("second", "旅行计划乙"), "第二答"),
        ],
    )
    close_open_epoch(config)

    first = await recall_context(config, "你还记得旅行计划吗？", "query-1")
    first_anchors = tuple(item.anchor_event_id for item in first.recall_frame.evidence)
    second = await recall_context(config, "你还记得旅行计划吗？", "query-2")
    second_anchors = tuple(item.anchor_event_id for item in second.recall_frame.evidence)

    connection = sqlite3.connect(config.database_path)
    try:
        first_sequences = tuple(
            connection.execute(
                "SELECT sequence_no FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            for event_id in first_anchors
        )
    finally:
        connection.close()

    assert first_sequences == tuple(sorted(first_sequences))
    assert second_anchors == first_anchors
    assert len(first.recall_frame.source_event_ids) == len(
        set(first.recall_frame.source_event_ids)
    )


async def test_ambiguous_and_no_match_are_distinct_and_prompt_forbids_fabrication(
    tmp_path,
) -> None:
    config = RuntimeConfig(tmp_path)
    ambiguous = await recall_context(config, "你还记得那个吗？", "ambiguous")
    no_match = await recall_context(config, "你还记得不存在的旅行计划吗？", "missing")

    assert ambiguous.recall_frame.uncertainty == "ambiguous"
    assert ambiguous.recall_frame.query_kind == "explicit_event"
    assert no_match.recall_frame.uncertainty == "no_match"
    assert no_match.recall_frame.query_kind == "explicit_phrase"
    assert "不要编造" in build_reply_messages(ambiguous)[-2]["content"]
    assert "不要编造" in build_reply_messages(no_match)[-2]["content"]


async def test_ambiguous_cue_does_not_execute_a_broad_query(
    tmp_path, monkeypatch
) -> None:
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["你说的是哪件事？"])
    runtime = RelationshipRuntime.open(config, model, clock=lambda: NOW)

    def unexpected_query(**_kwargs):
        raise AssertionError("ambiguous cue must not query recall")

    monkeypatch.setattr(runtime._ledger, "find_recall_episodes", unexpected_query)
    async with runtime:
        await collect(runtime, message("ambiguous", "你还记得那个吗？"))

    assert model.requests[0].context.recall_frame.uncertainty == "ambiguous"


async def test_all_evidence_ids_resolve_to_real_old_events(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    seed_turns(
        config,
        [(message("old", "旅行计划定在周日"), "我记下了。")],
    )
    close_open_epoch(config)

    context = await recall_context(config, "你还记得旅行计划吗？")
    ids = context.recall_frame.source_event_ids
    connection = sqlite3.connect(config.database_path)
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT e.event_id, ee.epoch_id
            FROM events AS e JOIN event_epochs AS ee ON ee.event_id = e.event_id
            WHERE e.event_id IN ({placeholders})
            """,
            ids,
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == len(ids)
    assert all(epoch_id != context.epoch_id for _event_id, epoch_id in rows)
