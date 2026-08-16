from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from runtime import RelationshipRuntime, RuntimeConfig, UserMessage
from runtime._prompt import build_reply_messages
from runtime.adapters import FakeReplyModel


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


def message(request_id: str, text: str) -> UserMessage:
    return UserMessage(request_id, "c1", text, NOW, "Asia/Shanghai")


async def collect(runtime: RelationshipRuntime, value: UserMessage):
    return [event async for event in runtime.handle_turn(value)]


def message_rows(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """
            SELECT event_id, conversation_id, actor, event_type
            FROM events WHERE event_type = 'message'
            ORDER BY sequence_no
            """
        ).fetchall()
    finally:
        connection.close()


async def test_normal_turn_rebuilds_recent_last_exchange_and_unresolved(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["固定回复"])
    async with RelationshipRuntime.open(
        config, model, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r1", "第一问"))
        await collect(runtime, message("r2", "第二问"))

    first = model.requests[0].context.working_activation
    second = model.requests[1].context.working_activation
    first_user_id = model.requests[0].user_event_id
    first_character_id = model.requests[1].context.history_messages[1].event_id
    second_user_id = model.requests[1].user_event_id

    assert first.recent_verbatim_event_ids == (first_user_id,)
    assert first.last_completed_user_event_id is None
    assert first.last_completed_character_event_id is None
    assert first.unresolved_user_event_ids == (first_user_id,)
    assert second.recent_verbatim_event_ids == (
        first_user_id,
        first_character_id,
        second_user_id,
    )
    assert second.last_completed_user_event_id == first_user_id
    assert second.last_completed_character_event_id == first_character_id
    assert second.unresolved_user_event_ids == (second_user_id,)

    rows = message_rows(config.database_path)
    ids = {row["event_id"] for row in rows}
    assert set(second.recent_verbatim_event_ids) <= ids
    assert all(row["conversation_id"] == "c1" for row in rows)


async def test_failed_turn_remains_unresolved_without_partial_character(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    failed = FakeReplyModel(["部分"], fail_after_chunks=1)
    async with RelationshipRuntime.open(
        config, failed, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r1", "失败问题"))

    next_model = FakeReplyModel(["正常"])
    async with RelationshipRuntime.open(
        config, next_model, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r2", "下一问"))

    activation = next_model.requests[0].context.working_activation
    rows = message_rows(config.database_path)
    assert [row["actor"] for row in rows] == ["user", "user", "character"]
    assert activation.recent_verbatim_event_ids == tuple(
        row["event_id"] for row in rows[:2]
    )
    assert activation.last_completed_user_event_id is None
    assert activation.last_completed_character_event_id is None
    assert activation.unresolved_user_event_ids == activation.recent_verbatim_event_ids


async def test_cancelled_turn_remains_unresolved_after_restart(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    release = asyncio.Event()
    cancelled = FakeReplyModel(["部分", "后续"], wait_event=release)
    runtime = RelationshipRuntime.open(config, cancelled, clock=lambda: NOW)
    stream = runtime.handle_turn(message("r1", "取消问题"))
    first_delta_task = asyncio.create_task(anext(stream))
    await cancelled.started.wait()
    release.set()
    await first_delta_task
    await stream.aclose()
    await runtime.close()

    restarted = FakeReplyModel(["正常"])
    async with RelationshipRuntime.open(
        config, restarted, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r2", "重启后"))

    activation = restarted.requests[0].context.working_activation
    assert len(activation.recent_verbatim_event_ids) == 2
    assert activation.last_completed_character_event_id is None
    assert activation.unresolved_user_event_ids == activation.recent_verbatim_event_ids


async def test_retry_reuses_same_unresolved_user_event(tmp_path) -> None:
    model = FakeReplyModel(["成功"], fail_after_chunks=0)
    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path), model, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r1", "重试我"))
        model.fail_after_chunks = None
        await collect(runtime, message("r1", "重试我"))

    first = model.requests[0].context.working_activation
    retried = model.requests[1].context.working_activation
    assert retried == first
    assert retried.recent_verbatim_event_ids == (model.requests[0].user_event_id,)


@pytest.mark.parametrize(
    "text",
    [
        "你好",
        "今天天气怎么样",
        "1+1等于几",
        "油锅着火怎么办",
        "昨天我去了公园",
    ],
)
async def test_normal_questions_do_not_create_recall_cues(tmp_path, text) -> None:
    model = FakeReplyModel(["回复"])
    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path), model, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r1", text))
    assert model.requests[0].context.working_activation.explicit_recall_cues == ()


async def test_explicit_phrase_and_time_cues_are_structured_and_deterministic(
    tmp_path,
) -> None:
    model = FakeReplyModel(["回复"])
    text = "你还记得上个月我说过的旅行计划吗？"
    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path), model, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r1", text))

    activation = model.requests[0].context.working_activation
    cues = activation.explicit_recall_cues
    assert [(cue.kind, cue.normalized_value) for cue in cues] == [
        ("intent", "explicit_recall"),
        ("time", "上个月"),
        ("phrase", "旅行计划"),
    ]
    time_cue = next(cue for cue in cues if cue.kind == "time")
    assert time_cue.range_start.isoformat() == "2026-07-01T00:00:00+08:00"
    assert time_cue.range_end.isoformat() == "2026-08-01T00:00:00+08:00"


async def test_ambiguous_recall_has_intent_but_no_executable_phrase(tmp_path) -> None:
    model = FakeReplyModel(["回复"])
    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path), model, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r1", "你还记得那个吗？"))
    cues = model.requests[0].context.working_activation.explicit_recall_cues
    assert [cue.kind for cue in cues] == ["intent"]


async def test_current_time_uses_injected_clock_and_dynamic_context_is_stable(
    tmp_path,
) -> None:
    model = FakeReplyModel(["回复"])
    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path), model, clock=lambda: NOW
    ) as runtime:
        await collect(runtime, message("r1", "固定输入"))

    context = model.requests[0].context
    activation = context.working_activation
    assert activation.current_time.isoformat() == "2026-08-04T12:00:00+08:00"
    assert activation.current_timezone == "Asia/Shanghai"
    assert not hasattr(activation, "current_topic")
    assert not hasattr(activation, "current_goals")
    assert not hasattr(activation, "mood_state")
    assert not hasattr(activation, "relationship_state")

    first = json.dumps(
        build_reply_messages(context), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    second = json.dumps(
        build_reply_messages(context), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert first == second
    dynamic = build_reply_messages(context)[-2]["content"]
    assert len(dynamic) < 180
    assert "event" not in dynamic
    assert "当前本地时间：2026-08-04T12:00:00+08:00" in dynamic
    assert model.calls == 1
    assert len(model.measured_contexts) == 6
    assert context.budget.total_prompt_tokens > 0
