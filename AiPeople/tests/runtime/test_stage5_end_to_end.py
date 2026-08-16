from __future__ import annotations

import logging
from datetime import datetime, timezone

from runtime import Completed, RelationshipRuntime, RuntimeConfig, UserMessage
from runtime._ledger import EventLedger
from runtime.adapters import FakeReplyModel


NOW = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)


def message(request_id: str, text: str) -> UserMessage:
    return UserMessage(request_id, "stage5-e2e", text, NOW, "Asia/Shanghai")


async def collect(runtime: RelationshipRuntime, value: UserMessage):
    return [event async for event in runtime.handle_turn(value)]


async def test_twenty_turns_and_restart_preserve_exact_epoch_order(tmp_path) -> None:
    config = RuntimeConfig(tmp_path)
    first_model = FakeReplyModel(["固定答复"])
    async with RelationshipRuntime.open(config, first_model, clock=lambda: NOW) as runtime:
        for index in range(20):
            events = await collect(runtime, message(f"r{index}", f"第{index}问"))
            assert isinstance(events[-1], Completed)
            assert events[-1].metrics.epoch_rolled_over is False

    assert len(first_model.requests) == 20
    for index, request in enumerate(first_model.requests):
        history = request.context.history_messages
        assert len(history) == index * 2
        assert [item.role for item in history] == ["user", "assistant"] * index
        assert [item.sequence_no for item in history] == list(
            range(1, index * 2 + 1)
        )
        assert request.context.current_user_text == f"第{index}问"
        assert request.context.current_user_event_id not in {
            item.event_id for item in history
        }

    restarted_model = FakeReplyModel(["重启答复"])
    async with RelationshipRuntime.open(
        config, restarted_model, clock=lambda: NOW
    ) as runtime:
        events = await collect(runtime, message("after-restart", "重启后第21问"))

    assert isinstance(events[-1], Completed)
    restarted = restarted_model.requests[0].context
    assert len(restarted.history_messages) == 40
    assert restarted.history_messages[0].text == "第0问"
    assert restarted.history_messages[-1].text == "固定答复"
    assert restarted.epoch_id == first_model.requests[-1].context.epoch_id


async def test_recall_logs_never_include_query_history_or_excerpt(
    tmp_path, caplog
) -> None:
    config = RuntimeConfig(tmp_path)
    ledger = EventLedger.open(config.database_path, clock=lambda: NOW)
    try:
        old = ledger.append_user_message(
            message("old", "PRIVATE-RECALL-EVIDENCE 的旅行计划")
        )
        ledger.append_character_message(
            request_id="old",
            conversation_id="stage5-e2e",
            user_event_id=old.event.event_id,
            text="PRIVATE-ASSISTANT-HISTORY",
        )
        ledger._connection.execute(
            """
            UPDATE conversation_epochs
            SET state='closed', closed_sequence_no=2,
                close_reason='context_budget', closed_at=?
            WHERE epoch_id=?
            """,
            (NOW.isoformat(), old.epoch_id),
        )
    finally:
        ledger.close()

    caplog.set_level(logging.INFO)
    model = FakeReplyModel(["PRIVATE-CURRENT-REPLY"])
    async with RelationshipRuntime.open(config, model, clock=lambda: NOW) as runtime:
        events = await collect(
            runtime,
            message("query", "你还记得 PRIVATE-RECALL-EVIDENCE 吗？"),
        )

    assert isinstance(events[-1], Completed)
    assert model.requests[0].context.recall_frame.evidence
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    for private_text in (
        "PRIVATE-RECALL-EVIDENCE",
        "PRIVATE-ASSISTANT-HISTORY",
        "PRIVATE-CURRENT-REPLY",
    ):
        assert private_text not in log_text

