from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime import Completed, RelationshipRuntime, RuntimeConfig, UserMessage
from runtime._context import ReplyContext
from runtime._ledger import EventLedger
from runtime.adapters import FakeReplyModel


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


def message(
    request_id: str,
    text: str,
    *,
    conversation_id: str = "c1",
) -> UserMessage:
    return UserMessage(
        request_id,
        conversation_id,
        text,
        NOW,
        "Asia/Shanghai",
    )


def projection_rows(path: Path) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        epochs = connection.execute(
            "SELECT * FROM conversation_epochs ORDER BY conversation_id, ordinal"
        ).fetchall()
        mappings = connection.execute(
            """
            SELECT ee.*, e.conversation_id, e.sequence_no
            FROM event_epochs AS ee
            JOIN events AS e ON e.event_id = ee.event_id
            ORDER BY e.conversation_id, e.sequence_no
            """
        ).fetchall()
        return epochs, mappings
    finally:
        connection.close()


async def collect(runtime: RelationshipRuntime, value: UserMessage):
    return [event async for event in runtime.handle_turn(value)]


def test_new_turn_atomically_maps_user_reply_and_status_to_user_epoch(tmp_path) -> None:
    path = tmp_path / "relationship.sqlite3"
    ledger = EventLedger.open(path)
    try:
        first = ledger.append_user_message(message("r1", "第一条"))
        reply = ledger.append_character_message(
            request_id="r1",
            conversation_id="c1",
            user_event_id=first.event.event_id,
            text="第一答",
        )
        failed = ledger.append_user_message(message("r2", "第二条"))
        status = ledger.append_turn_status(
            request_id="r2",
            conversation_id="c1",
            user_event_id=failed.event.event_id,
            event_type="turn_failed",
            code="model_unavailable",
            partial_text="部分",
        )
    finally:
        ledger.close()

    epochs, mappings = projection_rows(path)
    assert len(epochs) == 1
    assert epochs[0]["state"] == "open"
    assert {row["epoch_id"] for row in mappings} == {first.epoch_id}
    assert [row["event_id"] for row in mappings] == [
        first.event.event_id,
        reply.event_id,
        failed.event.event_id,
        status.event_id,
    ]
    assert [row["ordinal_in_epoch"] for row in mappings] == [1, 2, 3, 4]


def test_request_retry_reuses_event_and_epoch_mapping(tmp_path) -> None:
    path = tmp_path / "relationship.sqlite3"
    ledger = EventLedger.open(path)
    try:
        first = ledger.append_user_message(message("r1", "不重复"))
        retried = ledger.append_user_message(message("r1", "不重复"))
    finally:
        ledger.close()

    epochs, mappings = projection_rows(path)
    assert retried.created is False
    assert retried.event.event_id == first.event.event_id
    assert retried.epoch_id == first.epoch_id
    assert len(epochs) == 1
    assert len(mappings) == 1


def test_mapping_failure_rolls_back_the_corresponding_event(tmp_path, monkeypatch) -> None:
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    original = ledger._map_event_to_epoch

    def fail_mapping(_event_id: str, _epoch_id: str) -> None:
        raise sqlite3.IntegrityError("injected mapping failure")

    monkeypatch.setattr(ledger, "_map_event_to_epoch", fail_mapping)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="mapping failure"):
            ledger.append_user_message(message("r1", "不会半提交"))
        assert ledger.list_events() == []
    finally:
        monkeypatch.setattr(ledger, "_map_event_to_epoch", original)
        ledger.close()


def test_reply_mapping_failure_rolls_back_character_event(tmp_path, monkeypatch) -> None:
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    user = ledger.append_user_message(message("r1", "用户已提交"))

    def fail_mapping(_event_id: str, _epoch_id: str) -> None:
        raise sqlite3.IntegrityError("injected reply mapping failure")

    monkeypatch.setattr(ledger, "_map_event_to_epoch", fail_mapping)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="reply mapping failure"):
            ledger.append_character_message(
                request_id="r1",
                conversation_id="c1",
                user_event_id=user.event.event_id,
                text="不能半提交",
            )
        assert [event.actor for event in ledger.list_events()] == ["user"]
    finally:
        ledger.close()


def test_projection_constraints_reject_second_open_epoch_and_cross_conversation_mapping(
    tmp_path,
) -> None:
    path = tmp_path / "relationship.sqlite3"
    ledger = EventLedger.open(path)
    first = ledger.append_user_message(message("r1", "甲", conversation_id="c1"))
    second = ledger.append_user_message(message("r2", "乙", conversation_id="c2"))
    connection = ledger._connection
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO conversation_epochs (
                    epoch_id, conversation_id, ordinal, state,
                    opened_sequence_no, budget_version, created_at
                ) VALUES ('duplicate-open', 'c1', 2, 'open', 2, 1, ?)
                """,
                (NOW.isoformat(),),
            )
        connection.execute(
            "DELETE FROM event_epochs WHERE event_id = ?",
            (first.event.event_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="conversations differ"):
            connection.execute(
                """
                INSERT INTO event_epochs (
                    event_id, epoch_id, ordinal_in_epoch, estimated_tokens
                ) VALUES (?, ?, 2, 0)
                """,
                (first.event.event_id, second.epoch_id),
            )
    finally:
        ledger.close()


def test_deleted_projection_rebuilds_from_events_without_changing_events(tmp_path) -> None:
    path = tmp_path / "relationship.sqlite3"
    ledger = EventLedger.open(path)
    first = ledger.append_user_message(message("r1", "保留原文"))
    ledger.append_character_message(
        request_id="r1",
        conversation_id="c1",
        user_event_id=first.event.event_id,
        text="保留回复",
    )
    before = ledger.list_events("c1")

    rebuilt_epoch = ledger.rebuild_epoch_projection("c1")
    after = ledger.list_events("c1")
    snapshot = ledger.load_epoch_snapshot(first.event.event_id)
    ledger.close()

    assert rebuilt_epoch is not None
    assert rebuilt_epoch != first.epoch_id
    assert after == before
    assert [event.text for event in snapshot.history_events] == ["保留回复"]
    epochs, mappings = projection_rows(path)
    assert len(epochs) == 1
    assert len(mappings) == 2


def _create_stage4_database(path: Path) -> list[tuple[object, ...]]:
    migration = Path("runtime/migrations/001_event_ledger.sql").read_text(
        encoding="utf-8"
    )
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            ) STRICT;
            """
            + migration
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (1, ?)", (NOW.isoformat(),)
        )
        rows = [
            (
                "old-user",
                "old-request",
                "legacy",
                1,
                NOW.isoformat(),
                NOW.isoformat(),
                "Asia/Shanghai",
                "user",
                "message",
                json.dumps({"text": "旧用户原文", "status": "complete"}),
                "typed",
                None,
                None,
                1,
            ),
            (
                "old-reply",
                "old-request",
                "legacy",
                2,
                NOW.isoformat(),
                NOW.isoformat(),
                "UTC",
                "character",
                "message",
                json.dumps({"text": "旧角色原文", "status": "complete"}),
                "runtime",
                "old-user",
                None,
                1,
            ),
        ]
        connection.executemany(
            """
            INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()
        return connection.execute(
            "SELECT * FROM events ORDER BY sequence_no"
        ).fetchall()
    finally:
        connection.close()


def test_stage4_database_migrates_lazily_without_updating_old_events(tmp_path) -> None:
    path = tmp_path / "relationship.sqlite3"
    old_rows = _create_stage4_database(path)

    ledger = EventLedger.open(path)
    try:
        current = ledger.append_user_message(
            message("new-request", "新输入", conversation_id="legacy")
        )
        snapshot = ledger.load_epoch_snapshot(current.event.event_id)
    finally:
        ledger.close()

    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,), (4,), (5,), (6,), (7,), (8,)]
        assert connection.execute(
            "SELECT * FROM events WHERE sequence_no <= 2 ORDER BY sequence_no"
        ).fetchall() == old_rows
    finally:
        connection.close()
    assert [event.text for event in snapshot.history_events] == [
        "旧用户原文",
        "旧角色原文",
    ]
    epochs, mappings = projection_rows(path)
    assert len(epochs) == 1
    assert len(mappings) == 3


async def test_second_turn_receives_first_complete_exchange_once(tmp_path) -> None:
    model = FakeReplyModel(["角色回复"])
    async with RelationshipRuntime.open(RuntimeConfig(tmp_path), model) as runtime:
        await collect(runtime, message("r1", "第一问"))
        await collect(runtime, message("r2", "第二问"))

    first, second = model.requests
    assert first.context.history_messages == ()
    assert [(item.role, item.text) for item in second.context.history_messages] == [
        ("user", "第一问"),
        ("assistant", "角色回复"),
    ]
    assert second.context.current_user_text == "第二问"
    assert all(
        item.event_id != second.context.current_user_event_id
        for item in second.context.history_messages
    )
    assert first.context.epoch_id == second.context.epoch_id


async def test_restart_restores_current_epoch_history(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    first_model = FakeReplyModel(["第一答"])
    async with RelationshipRuntime.open(config, first_model) as runtime:
        await collect(runtime, message("r1", "第一问"))

    second_model = FakeReplyModel(["第二答"])
    async with RelationshipRuntime.open(config, second_model) as runtime:
        await collect(runtime, message("r2", "重启后追问"))

    context = second_model.requests[0].context
    assert [(item.role, item.text) for item in context.history_messages] == [
        ("user", "第一问"),
        ("assistant", "第一答"),
    ]


async def test_failed_partial_reply_is_excluded_but_user_message_remains(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    failed_model = FakeReplyModel(["部分", "不应完整"], fail_after_chunks=1)
    async with RelationshipRuntime.open(config, failed_model) as runtime:
        await collect(runtime, message("r1", "失败前问题"))

    next_model = FakeReplyModel(["正常回复"])
    async with RelationshipRuntime.open(config, next_model) as runtime:
        await collect(runtime, message("r2", "下一问"))

    context = next_model.requests[0].context
    assert [(item.role, item.text) for item in context.history_messages] == [
        ("user", "失败前问题")
    ]
    assert all(item.text != "部分" for item in context.history_messages)


async def test_cancelled_partial_reply_is_excluded_but_user_message_remains(
    tmp_path,
) -> None:
    config = RuntimeConfig(tmp_path)
    release = asyncio.Event()
    cancelled_model = FakeReplyModel(["已经显示", "不会完成"], wait_event=release)
    runtime = RelationshipRuntime.open(config, cancelled_model)
    stream = runtime.handle_turn(message("r1", "取消前问题"))
    task = asyncio.create_task(anext(stream))
    await cancelled_model.started.wait()
    release.set()
    first_delta = await task
    assert first_delta.text == "已经显示"
    await stream.aclose()
    await runtime.close()

    next_model = FakeReplyModel(["正常回复"])
    async with RelationshipRuntime.open(config, next_model) as reopened:
        await collect(reopened, message("r2", "下一问"))

    context = next_model.requests[0].context
    assert [(item.role, item.text) for item in context.history_messages] == [
        ("user", "取消前问题")
    ]
    assert all(item.text != "已经显示" for item in context.history_messages)


class _OrderedModel:
    context_size = 4096

    def __init__(self) -> None:
        self.requests = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def measure_prompt(self, _context: ReplyContext) -> int:
        return 1

    async def stream_reply(self, request):
        self.requests.append(request)
        if request.request_id == "r1":
            self.first_started.set()
            await self.release_first.wait()
        yield f"reply-{request.request_id}"


async def test_same_conversation_turns_are_serialized_through_character_commit(
    tmp_path,
) -> None:
    model = _OrderedModel()
    async with RelationshipRuntime.open(RuntimeConfig(tmp_path), model) as runtime:
        first = asyncio.create_task(collect(runtime, message("r1", "第一问")))
        await model.first_started.wait()
        second = asyncio.create_task(collect(runtime, message("r2", "第二问")))
        await asyncio.sleep(0.05)
        assert [request.request_id for request in model.requests] == ["r1"]
        model.release_first.set()
        first_result, second_result = await asyncio.gather(first, second)

    assert isinstance(first_result[-1], Completed)
    assert isinstance(second_result[-1], Completed)
    second_context = model.requests[1].context
    assert [(item.role, item.text) for item in second_context.history_messages] == [
        ("user", "第一问"),
        ("assistant", "reply-r1"),
    ]


async def test_cancelled_conversation_waiter_releases_guard_state(tmp_path) -> None:
    model = _OrderedModel()
    async with RelationshipRuntime.open(RuntimeConfig(tmp_path), model) as runtime:
        first = asyncio.create_task(collect(runtime, message("r1", "第一问")))
        await model.first_started.wait()
        waiting = asyncio.create_task(collect(runtime, message("r2", "等待中")))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        model.release_first.set()
        await first
        third = await collect(runtime, message("r3", "取消后继续"))
        assert isinstance(third[-1], Completed)
        assert runtime._conversation_slots == {}
        assert runtime._active_requests == set()


async def test_different_conversations_can_enter_model_concurrently(tmp_path) -> None:
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def on_request(_request) -> None:
        if model.calls == 2:
            both_entered.set()

    model = FakeReplyModel(["回复"], on_request=on_request, wait_event=release)
    async with RelationshipRuntime.open(RuntimeConfig(tmp_path), model) as runtime:
        first = asyncio.create_task(
            collect(runtime, message("r1", "甲", conversation_id="c1"))
        )
        second = asyncio.create_task(
            collect(runtime, message("r2", "乙", conversation_id="c2"))
        )
        await asyncio.wait_for(both_entered.wait(), timeout=1)
        release.set()
        await asyncio.gather(first, second)

    assert model.calls == 2
    assert all(request.context.history_messages == () for request in model.requests)
