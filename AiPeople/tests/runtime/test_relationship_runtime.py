from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone

import pytest

from runtime import Completed, Failed, RelationshipRuntime, RuntimeConfig, TextDelta, UserMessage
from runtime.adapters import FakeReplyModel


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


def message(request_id: str = "r1", text: str = "你好") -> UserMessage:
    return UserMessage(request_id, "c1", text, NOW, "Asia/Shanghai")


async def collect(runtime: RelationshipRuntime, value: UserMessage):
    return [event async for event in runtime.handle_turn(value)]


def database_events(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM events ORDER BY conversation_id, sequence_no"
        ).fetchall()
    finally:
        connection.close()


async def test_normal_turn_streams_then_completes(tmp_path):
    model = FakeReplyModel(["甲", "乙", "丙"])
    config = RuntimeConfig(tmp_path)
    async with RelationshipRuntime.open(config, model) as runtime:
        events = await collect(runtime, message())

    assert [event.text for event in events if isinstance(event, TextDelta)] == [
        "甲",
        "乙",
        "丙",
    ]
    completed = events[-1]
    assert isinstance(completed, Completed)
    assert completed.text == "甲乙丙"
    assert completed.metrics.delta_count == 3
    assert completed.metrics.output_chars == 3
    assert completed.metrics.replayed is False
    assert model.start_calls == 1
    assert model.close_calls == 1


async def test_runtime_lifecycle_is_idempotent(tmp_path):
    model = FakeReplyModel(["回复"])
    runtime = RelationshipRuntime.open(RuntimeConfig(tmp_path), model)

    await runtime.start()
    await runtime.start()
    await runtime.close()
    await runtime.close()

    assert model.start_calls == 1
    assert model.close_calls == 1


async def test_model_start_failure_releases_database_lock(tmp_path):
    class StartFailure:
        async def start(self):
            raise RuntimeError("configured start failure")

        async def close(self):
            return None

        async def stream_reply(self, _request):
            yield "never"

    config = RuntimeConfig(tmp_path)
    runtime = RelationshipRuntime.open(config, StartFailure())
    with pytest.raises(RuntimeError, match="configured start failure"):
        await runtime.start()

    reopened = RelationshipRuntime.open(config, FakeReplyModel(["ok"]))
    await reopened.close()


async def test_user_event_is_committed_before_model_starts(tmp_path):
    config = RuntimeConfig(tmp_path)
    observed = []

    def inspect_ledger(request):
        rows = database_events(config.database_path)
        observed.extend(rows)
        assert len(rows) == 1
        assert rows[0]["event_id"] == request.user_event_id
        assert rows[0]["actor"] == "user"

    model = FakeReplyModel(["回复"], on_request=inspect_ledger)
    async with RelationshipRuntime.open(config, model) as runtime:
        await collect(runtime, message())
    assert len(observed) == 1


async def test_completed_turn_survives_restart_and_replays_without_model(tmp_path):
    config = RuntimeConfig(tmp_path)
    first_model = FakeReplyModel(["完整", "回复"])
    async with RelationshipRuntime.open(config, first_model) as runtime:
        first = await collect(runtime, message())

    second_model = FakeReplyModel(["不应调用"])
    async with RelationshipRuntime.open(config, second_model) as runtime:
        replay = await collect(runtime, message())

    assert second_model.calls == 0
    assert [event.text for event in replay if isinstance(event, TextDelta)] == ["完整回复"]
    assert isinstance(replay[-1], Completed)
    assert replay[-1].metrics.replayed is True
    assert replay[-1].assistant_event_id == first[-1].assistant_event_id
    assert len(database_events(config.database_path)) == 2


async def test_model_failure_records_partial_text_without_complete_reply(tmp_path):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["部分", "文字", "结尾"], fail_after_chunks=2)
    async with RelationshipRuntime.open(config, model) as runtime:
        events = await collect(runtime, message())

    assert [event.text for event in events if isinstance(event, TextDelta)] == ["部分", "文字"]
    assert isinstance(events[-1], Failed)
    assert events[-1].code == "model_unavailable"
    rows = database_events(config.database_path)
    assert [row["event_type"] for row in rows] == ["message", "turn_failed"]
    payload = json.loads(rows[-1]["payload_json"])
    assert payload["partial_text"] == "部分文字"
    assert not any(row["actor"] == "character" for row in rows)


@pytest.mark.parametrize("chunks", [[], [""], ["   "]])
async def test_empty_model_response_is_a_failure(tmp_path, chunks):
    config = RuntimeConfig(tmp_path)
    async with RelationshipRuntime.open(config, FakeReplyModel(chunks)) as runtime:
        events = await collect(runtime, message())
    assert isinstance(events[-1], Failed)
    assert events[-1].code == "empty_model_response"
    assert not any(row["actor"] == "character" for row in database_events(config.database_path))


async def test_cancellation_records_partial_text_and_reraises(tmp_path):
    config = RuntimeConfig(tmp_path)
    waiting = asyncio.Event()
    never_release = asyncio.Event()

    class CancellableAfterOneChunk:
        async def measure_prompt(self, _context):
            return 1

        async def stream_reply(self, _request):
            yield "已经显示"
            waiting.set()
            await never_release.wait()

    model = CancellableAfterOneChunk()
    runtime = RelationshipRuntime.open(config, model)
    task = asyncio.create_task(collect(runtime, message()))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await runtime.close()

    rows = database_events(config.database_path)
    assert [row["event_type"] for row in rows] == ["message", "generation_cancelled"]
    assert json.loads(rows[-1]["payload_json"])["partial_text"] == "已经显示"


async def test_closing_stream_records_partial_text_as_cancelled(tmp_path):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["第一段", "第二段"])
    runtime = RelationshipRuntime.open(config, model)
    stream = runtime.handle_turn(message())
    first = await anext(stream)
    assert first == TextDelta("r1", "第一段")
    await stream.aclose()
    await runtime.close()

    rows = database_events(config.database_path)
    assert [row["event_type"] for row in rows] == ["message", "generation_cancelled"]
    assert json.loads(rows[-1]["payload_json"])["partial_text"] == "第一段"


async def test_failed_turn_retries_without_duplicate_user_event(tmp_path):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["成功"], fail_after_chunks=0)
    async with RelationshipRuntime.open(config, model) as runtime:
        first = await collect(runtime, message())
        assert isinstance(first[-1], Failed)
        model.fail_after_chunks = None
        second = await collect(runtime, message())
        assert isinstance(second[-1], Completed)

    rows = database_events(config.database_path)
    assert sum(row["actor"] == "user" for row in rows) == 1
    assert sum(row["actor"] == "character" for row in rows) == 1
    assert model.calls == 2


async def test_changed_input_with_same_request_id_conflicts(tmp_path):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["回复"])
    async with RelationshipRuntime.open(config, model) as runtime:
        await collect(runtime, message())
        events = await collect(runtime, message(text="不同文字"))
    assert events == [Failed("r1", None, "request_conflict", False)]
    assert model.calls == 1


async def test_user_commit_failure_never_calls_model(tmp_path, monkeypatch):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["回复"])
    async with RelationshipRuntime.open(config, model) as runtime:
        def fail(_message):
            raise sqlite3.OperationalError("injected write failure")

        monkeypatch.setattr(runtime._ledger, "append_user_message", fail)
        events = await collect(runtime, message())
    assert events == [Failed("r1", None, "ledger_unavailable", True)]
    assert model.calls == 0


async def test_reply_commit_failure_never_emits_completed(tmp_path, monkeypatch):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["已经显示"])
    async with RelationshipRuntime.open(config, model) as runtime:
        def fail(**_kwargs):
            raise sqlite3.OperationalError("injected reply failure")

        monkeypatch.setattr(runtime._ledger, "append_character_message", fail)
        events = await collect(runtime, message())

    assert isinstance(events[0], TextDelta)
    assert events[-1] == Failed("r1", events[-1].user_event_id, "commit_failed", True)
    assert not any(isinstance(event, Completed) for event in events)
    rows = database_events(config.database_path)
    assert [row["event_type"] for row in rows] == ["message", "turn_failed"]


async def test_concurrent_duplicate_request_calls_model_once(tmp_path):
    config = RuntimeConfig(tmp_path)
    gate = asyncio.Event()
    model = FakeReplyModel(["完成"], wait_event=gate)
    async with RelationshipRuntime.open(config, model) as runtime:
        first_task = asyncio.create_task(collect(runtime, message()))
        await model.started.wait()
        duplicate = await collect(runtime, message())
        gate.set()
        first = await first_task

    assert duplicate == [Failed("r1", None, "turn_in_progress", True)]
    assert isinstance(first[-1], Completed)
    assert model.calls == 1


async def test_invalid_input_returns_failed_without_writing(tmp_path):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["回复"])
    async with RelationshipRuntime.open(config, model) as runtime:
        events = await collect(runtime, message(text="  \n"))
    assert events == [Failed("r1", None, "invalid_input", False)]
    assert model.calls == 0
    assert database_events(config.database_path) == []


async def test_logs_do_not_contain_private_text(tmp_path, caplog):
    config = RuntimeConfig(tmp_path)
    model = FakeReplyModel(["PRIVATE-ASSISTANT-TEXT"])
    caplog.set_level(logging.INFO)
    async with RelationshipRuntime.open(config, model) as runtime:
        await collect(runtime, message(text="PRIVATE-USER-TEXT"))
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "PRIVATE-USER-TEXT" not in log_text
    assert "PRIVATE-ASSISTANT-TEXT" not in log_text
