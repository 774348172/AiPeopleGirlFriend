from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from runtime import Completed, Failed, RelationshipRuntime, RuntimeConfig, UserMessage
from runtime._context import (
    ContextAssembler,
    ContextBudgetExceeded,
    RecallEvidence,
    RecallFrame,
    initial_reply_context,
)
from runtime._ledger import EventLedger
from runtime._prompt import build_reply_messages
from runtime.adapters import FakeReplyModel


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


def message(request_id: str, text: str) -> UserMessage:
    return UserMessage(request_id, "c1", text, NOW, "Asia/Shanghai")


async def collect(runtime: RelationshipRuntime, value: UserMessage):
    return [event async for event in runtime.handle_turn(value)]


def assembler(ledger: EventLedger, *, recall_target_tokens: int = 32):
    return ContextAssembler(
        ledger=ledger,
        context_size=120,
        reply_reserve_tokens=10,
        safety_margin_tokens=10,
        recall_candidate_limit=20,
        recall_evidence_limit=6,
        recall_excerpt_chars=240,
        recall_target_tokens=recall_target_tokens,
    )


@pytest.mark.parametrize("measured_tokens", [99, 100])
async def test_final_budget_accepts_one_below_and_exact_limit(
    tmp_path, measured_tokens
) -> None:
    ledger = EventLedger.open(tmp_path / "ledger.sqlite3", clock=lambda: NOW)
    try:
        context = initial_reply_context(
            user_event_id="current",
            text="逐字符保留",
            occurred_at=NOW,
            timezone="Asia/Shanghai",
            context_size=120,
            reply_reserve_tokens=10,
            safety_margin_tokens=10,
        )

        async def measure(_context):
            return measured_tokens

        fitted = await assembler(ledger).fit_to_budget(context, measure)
    finally:
        ledger.close()

    assert fitted.budget.total_prompt_tokens == measured_tokens
    assert fitted.budget.maximum_prompt_tokens == 100
    assert fitted.budget.fits is True


async def test_final_budget_rejects_one_token_over_limit(tmp_path) -> None:
    ledger = EventLedger.open(tmp_path / "ledger.sqlite3", clock=lambda: NOW)
    try:
        context = initial_reply_context(
            user_event_id="current",
            text="逐字符保留",
            occurred_at=NOW,
            timezone="Asia/Shanghai",
            context_size=120,
            reply_reserve_tokens=10,
            safety_margin_tokens=10,
        )

        async def measure(_context):
            return 101

        with pytest.raises(ContextBudgetExceeded) as captured:
            await assembler(ledger).fit_to_budget(context, measure)
    finally:
        ledger.close()

    assert captured.value.prompt_tokens == 101
    assert captured.value.maximum_prompt_tokens == 100


async def test_recall_budget_drops_only_complete_low_ranked_evidence(tmp_path) -> None:
    ledger = EventLedger.open(tmp_path / "ledger.sqlite3", clock=lambda: NOW)
    try:
        evidence = tuple(
            RecallEvidence(
                anchor_event_id=f"anchor-{index}",
                event_ids=(f"anchor-{index}",),
                occurred_at=NOW,
                actor="user",
                verbatim_excerpt=f"完整证据-{index}",
                rank_reasons=("stable_sequence",),
            )
            for index in range(3)
        )
        base = initial_reply_context(
            user_event_id="current",
            text="你还记得吗",
            occurred_at=NOW,
            timezone="Asia/Shanghai",
            context_size=120,
            reply_reserve_tokens=10,
            safety_margin_tokens=10,
        )
        context = replace(
            base,
            recall_frame=RecallFrame(
                evidence,
                tuple(item.anchor_event_id for item in evidence),
                "none",
                "explicit_phrase",
            ),
        )

        async def measure(value):
            return (
                10
                + (5 if value.dynamic_context_enabled else 0)
                + 20 * len(value.recall_frame.evidence)
            )

        fitted = await assembler(
            ledger, recall_target_tokens=25
        ).fit_to_budget(context, measure)
    finally:
        ledger.close()

    assert [item.verbatim_excerpt for item in fitted.recall_frame.evidence] == [
        "完整证据-0"
    ]
    assert fitted.recall_frame.source_event_ids == ("anchor-0",)
    assert fitted.budget.recall_tokens == 20


class BudgetFakeModel(FakeReplyModel):
    async def measure_prompt(self, context):
        self.measured_contexts.append(context)
        role_text_tokens = sum(
            len(item["content"])
            for item in build_reply_messages(context)
            if item["role"] != "system"
        )
        return (
            20
            + role_text_tokens
            + (5 if context.dynamic_context_enabled else 0)
            + 10 * len(context.recall_frame.evidence)
        )


def small_config(tmp_path) -> RuntimeConfig:
    return RuntimeConfig(
        tmp_path,
        context_size=100,
        reply_reserve_tokens=10,
        context_safety_margin_tokens=10,
        recall_target_tokens=15,
        recent_verbatim_target_tokens=20,
    )


async def test_runtime_rolls_over_once_then_recalls_immutable_old_events(
    tmp_path,
) -> None:
    config = small_config(tmp_path)
    model = BudgetFakeModel(["答复"], context_size=100)
    async with RelationshipRuntime.open(config, model, clock=lambda: NOW) as runtime:
        first = await collect(runtime, message("r1", "旅行计划" + "甲" * 26))
        second = await collect(runtime, message("r2", "乙" * 30))
        third = await collect(runtime, message("r3", "你还记得旅行计划吗？"))

    first_completed = first[-1]
    second_completed = second[-1]
    third_completed = third[-1]
    assert isinstance(first_completed, Completed)
    assert isinstance(second_completed, Completed)
    assert isinstance(third_completed, Completed)
    assert first_completed.metrics.epoch_rolled_over is False
    assert second_completed.metrics.epoch_rolled_over is True
    assert third_completed.metrics.epoch_rolled_over is False
    assert second_completed.metrics.history_event_count == 0
    assert third_completed.metrics.recall_evidence_count == 1
    assert third_completed.metrics.prompt_tokens <= config.maximum_prompt_tokens
    assert model.requests[1].context.history_messages == ()
    assert "旅行计划" in model.requests[2].context.recall_frame.evidence[0].verbatim_excerpt

    connection = sqlite3.connect(config.database_path)
    connection.row_factory = sqlite3.Row
    try:
        epochs = connection.execute(
            "SELECT * FROM conversation_epochs ORDER BY ordinal"
        ).fetchall()
        events = connection.execute(
            "SELECT event_id, sequence_no FROM events WHERE event_type='message' ORDER BY sequence_no"
        ).fetchall()
    finally:
        connection.close()

    assert [(row["ordinal"], row["state"]) for row in epochs] == [
        (1, "closed"),
        (2, "open"),
    ]
    assert epochs[0]["closed_sequence_no"] == 2
    assert epochs[1]["opened_sequence_no"] == 3
    assert [row["sequence_no"] for row in events] == [1, 2, 3, 4, 5, 6]


async def test_single_overlong_input_is_committed_but_never_generated(tmp_path) -> None:
    config = small_config(tmp_path)
    model = BudgetFakeModel(["不应生成"], context_size=100)
    async with RelationshipRuntime.open(config, model, clock=lambda: NOW) as runtime:
        events = await collect(runtime, message("long", "长" * 100))

    assert isinstance(events[-1], Failed)
    assert events[-1].code == "context_budget_exceeded"
    assert events[-1].retryable is True
    assert model.calls == 0
    connection = sqlite3.connect(config.database_path)
    try:
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM events ORDER BY sequence_no"
            ).fetchall()
        ]
        epoch_states = connection.execute(
            "SELECT state FROM conversation_epochs ORDER BY ordinal"
        ).fetchall()
    finally:
        connection.close()
    assert event_types == ["message", "turn_failed"]
    assert epoch_states == [("open",)]


def test_rollover_and_user_mapping_roll_back_as_one_transaction(
    tmp_path, monkeypatch
) -> None:
    ledger = EventLedger.open(tmp_path / "ledger.sqlite3", clock=lambda: NOW)
    try:
        first = ledger.append_user_message(message("r1", "旧问题"))
        ledger.append_character_message(
            request_id="r1",
            conversation_id="c1",
            user_event_id=first.event.event_id,
            text="旧回答",
        )
        preview = ledger.preview_user_message(message("r2", "新问题"))

        def fail_mapping(_event_id, _epoch_id):
            raise sqlite3.OperationalError("injected mapping failure")

        monkeypatch.setattr(ledger, "_map_event_to_epoch", fail_mapping)
        with pytest.raises(sqlite3.OperationalError, match="mapping failure"):
            ledger.append_user_message(
                message("r2", "新问题"),
                rollover_from_epoch_id=preview.epoch_id,
            )

        epochs = ledger._connection.execute(
            "SELECT epoch_id, state FROM conversation_epochs ORDER BY ordinal"
        ).fetchall()
        events = ledger.list_events("c1")
    finally:
        ledger.close()

    assert [(row["epoch_id"], row["state"]) for row in epochs] == [
        (preview.epoch_id, "open")
    ]
    assert [event.request_id for event in events] == ["r1", "r1"]


async def test_completed_replay_does_not_measure_or_roll_over_again(tmp_path) -> None:
    config = small_config(tmp_path)
    first_model = BudgetFakeModel(["答复"], context_size=100)
    async with RelationshipRuntime.open(config, first_model, clock=lambda: NOW) as runtime:
        first = await collect(runtime, message("r1", "正常输入"))

    replay_model = BudgetFakeModel(["不应调用"], context_size=100)
    async with RelationshipRuntime.open(config, replay_model, clock=lambda: NOW) as runtime:
        replay = await collect(runtime, message("r1", "正常输入"))

    assert isinstance(first[-1], Completed)
    assert isinstance(replay[-1], Completed)
    assert replay[-1].metrics.replayed is True
    assert replay[-1].metrics.epoch_rolled_over is False
    assert replay_model.calls == 0
    assert replay_model.measured_contexts == []


async def test_failed_generation_retry_reuses_the_rollover_epoch(tmp_path) -> None:
    config = small_config(tmp_path)
    model = BudgetFakeModel(["答复"], context_size=100)
    async with RelationshipRuntime.open(config, model, clock=lambda: NOW) as runtime:
        await collect(runtime, message("r1", "旅行计划" + "甲" * 26))
        model.fail_after_chunks = 0
        failed = await collect(runtime, message("r2", "乙" * 30))
        model.fail_after_chunks = None
        retried = await collect(runtime, message("r2", "乙" * 30))

    assert isinstance(failed[-1], Failed)
    assert failed[-1].code == "model_unavailable"
    assert isinstance(retried[-1], Completed)
    assert retried[-1].metrics.epoch_rolled_over is False

    connection = sqlite3.connect(config.database_path)
    try:
        epochs = connection.execute(
            "SELECT ordinal, state FROM conversation_epochs ORDER BY ordinal"
        ).fetchall()
        user_count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE actor='user'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert epochs == [(1, "closed"), (2, "open")]
    assert user_count == 2
