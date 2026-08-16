from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from runtime import UserMessage
from runtime._ledger import EventLedger, LedgerError, RequestConflictError
from runtime._plans import PlanStateError, require_initial_state, require_transition


NOW = datetime(2026, 8, 5, 1, 2, 3, tzinfo=timezone.utc)
DUE = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


def _message(request_id: str, conversation_id: str = "conversation-a") -> UserMessage:
    return UserMessage(
        request_id=request_id,
        conversation_id=conversation_id,
        text=f"evidence-{request_id}",
        occurred_at=NOW,
        timezone="Asia/Shanghai",
    )


def _ledger(tmp_path):
    ids = iter(str(index) for index in range(1, 100))
    return EventLedger.open(
        tmp_path / "relationship.sqlite3",
        clock=lambda: NOW,
        id_factory=lambda: next(ids),
    )


@pytest.mark.parametrize("state", ["proposed", "confirmed"])
def test_initial_states_are_explicit(state: str) -> None:
    require_initial_state(state)


@pytest.mark.parametrize("state", ["due", "completed", "cancelled", "missed", "bad"])
def test_invalid_initial_states_are_rejected(state: str) -> None:
    with pytest.raises(PlanStateError):
        require_initial_state(state)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("proposed", "confirmed"),
        ("proposed", "cancelled"),
        ("confirmed", "due"),
        ("confirmed", "completed"),
        ("confirmed", "cancelled"),
        ("confirmed", "missed"),
        ("due", "completed"),
        ("due", "cancelled"),
        ("due", "missed"),
    ],
)
def test_allowed_state_transitions(source: str, target: str) -> None:
    require_transition(source, target)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("proposed", "due"),
        ("proposed", "completed"),
        ("confirmed", "proposed"),
        ("due", "confirmed"),
        ("completed", "confirmed"),
        ("cancelled", "confirmed"),
        ("missed", "confirmed"),
        ("confirmed", "confirmed"),
        ("bad", "confirmed"),
    ],
)
def test_invalid_state_transitions_are_rejected(source: str, target: str) -> None:
    with pytest.raises(PlanStateError):
        require_transition(source, target)


@pytest.mark.parametrize("kind", ["promise", "reminder", "shared_plan"])
def test_create_and_list_all_plan_kinds(tmp_path, kind: str) -> None:
    ledger = _ledger(tmp_path)
    try:
        event = ledger.append_user_message(_message(f"create-{kind}")).event
        plan = ledger.create_plan(
            conversation_id=event.conversation_id,
            kind=kind,
            description=f" {kind} description ",
            due_at=DUE if kind == "reminder" else None,
            timezone_name="Asia/Shanghai",
            source_event_id=event.event_id,
            idempotency_key=f"create:{kind}",
            initial_state="confirmed",
        )
        assert plan.description == f"{kind} description"
        assert plan.created_from_event_id == event.event_id
        assert plan.updated_from_event_id == event.event_id
        assert plan.version == 1
        assert ledger.list_active_plans(event.conversation_id) == (plan,)
    finally:
        ledger.close()


def test_transition_is_atomic_ordered_and_idempotent(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    try:
        create_event = ledger.append_user_message(_message("create")).event
        plan = ledger.create_plan(
            conversation_id=create_event.conversation_id,
            kind="reminder",
            description="明天提醒交材料",
            due_at=None,
            timezone_name="Asia/Shanghai",
            source_event_id=create_event.event_id,
            idempotency_key="create:reminder",
        )
        confirm_event = ledger.append_user_message(_message("confirm")).event
        confirmed = ledger.transition_plan(
            plan_id=plan.plan_id,
            to_state="confirmed",
            source_event_id=confirm_event.event_id,
            idempotency_key="confirm:reminder",
            due_at=DUE,
        )
        replayed = ledger.transition_plan(
            plan_id=plan.plan_id,
            to_state="confirmed",
            source_event_id=confirm_event.event_id,
            idempotency_key="confirm:reminder",
            due_at=DUE,
        )
        assert confirmed == replayed
        assert confirmed.version == 2
        assert confirmed.updated_from_event_id == confirm_event.event_id
        with pytest.raises(RequestConflictError):
            ledger.transition_plan(
                plan_id=plan.plan_id,
                to_state="confirmed",
                source_event_id=confirm_event.event_id,
                idempotency_key="confirm:reminder",
                due_at=datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
            )
        with sqlite3.connect(ledger.database_path) as connection:
            rows = connection.execute(
                "SELECT ordinal, from_state, to_state FROM plan_transitions ORDER BY ordinal"
            ).fetchall()
        assert rows == [(1, None, "proposed"), (2, "proposed", "confirmed")]
    finally:
        ledger.close()


def test_invalid_evidence_time_and_idempotency_conflicts_roll_back(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    try:
        event_a = ledger.append_user_message(_message("a")).event
        event_b = ledger.append_user_message(_message("b", "conversation-b")).event
        with pytest.raises(RequestConflictError):
            ledger.create_plan(
                conversation_id="conversation-a",
                kind="promise",
                description="跨会话",
                due_at=None,
                timezone_name="Asia/Shanghai",
                source_event_id=event_b.event_id,
                idempotency_key="cross-conversation",
            )
        with pytest.raises(LedgerError):
            ledger.create_plan(
                conversation_id="conversation-a",
                kind="promise",
                description="未知证据",
                due_at=None,
                timezone_name="Asia/Shanghai",
                source_event_id="missing",
                idempotency_key="missing-evidence",
            )
        with pytest.raises(ValueError):
            ledger.create_plan(
                conversation_id="conversation-a",
                kind="promise",
                description="坏时区",
                due_at=None,
                timezone_name="Mars/Olympus",
                source_event_id=event_a.event_id,
                idempotency_key="bad-timezone",
            )
        with pytest.raises(PlanStateError):
            ledger.create_plan(
                conversation_id="conversation-a",
                kind="reminder",
                description="缺时间",
                due_at=None,
                timezone_name="Asia/Shanghai",
                source_event_id=event_a.event_id,
                idempotency_key="missing-due",
                initial_state="confirmed",
            )
        plan = ledger.create_plan(
            conversation_id="conversation-a",
            kind="promise",
            description="原命令",
            due_at=None,
            timezone_name="Asia/Shanghai",
            source_event_id=event_a.event_id,
            idempotency_key="same-key",
        )
        with pytest.raises(RequestConflictError):
            ledger.create_plan(
                conversation_id="conversation-a",
                kind="promise",
                description="冲突命令",
                due_at=None,
                timezone_name="Asia/Shanghai",
                source_event_id=event_a.event_id,
                idempotency_key="same-key",
            )
        assert ledger.list_active_plans("conversation-a") == (plan,)
    finally:
        ledger.close()


def test_projection_rebuild_preserves_current_bytes(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    try:
        create_event = ledger.append_user_message(_message("create")).event
        plan = ledger.create_plan(
            conversation_id="conversation-a",
            kind="shared_plan",
            description="周末一起做饭",
            due_at=DUE,
            timezone_name="Asia/Shanghai",
            source_event_id=create_event.event_id,
            idempotency_key="create",
            initial_state="confirmed",
        )
        complete_event = ledger.append_user_message(_message("complete")).event
        completed = ledger.transition_plan(
            plan_id=plan.plan_id,
            to_state="completed",
            source_event_id=complete_event.event_id,
            idempotency_key="complete",
        )
        with sqlite3.connect(ledger.database_path) as connection:
            before = connection.execute("SELECT * FROM plans ORDER BY plan_id").fetchall()
            event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            transition_count = connection.execute(
                "SELECT COUNT(*) FROM plan_transitions"
            ).fetchone()[0]
            connection.execute("DELETE FROM plans")
            connection.commit()
        assert ledger.rebuild_plan_projection() == 1
        with sqlite3.connect(ledger.database_path) as connection:
            after = connection.execute("SELECT * FROM plans ORDER BY plan_id").fetchall()
            assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == event_count
            assert connection.execute(
                "SELECT COUNT(*) FROM plan_transitions"
            ).fetchone()[0] == transition_count
        assert before == after
        assert ledger.list_active_plans("conversation-a") == ()
        assert completed.state == "completed"
    finally:
        ledger.close()


def test_transition_journal_rejects_update_and_delete(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    try:
        event = ledger.append_user_message(_message("create")).event
        ledger.create_plan(
            conversation_id="conversation-a",
            kind="promise",
            description="不改日志",
            due_at=None,
            timezone_name="Asia/Shanghai",
            source_event_id=event.event_id,
            idempotency_key="create",
        )
        with sqlite3.connect(ledger.database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute("UPDATE plan_transitions SET description='changed'")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute("DELETE FROM plan_transitions")
    finally:
        ledger.close()
