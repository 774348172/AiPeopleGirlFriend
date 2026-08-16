from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from runtime._ledger import EventLedger, EventRecord
from runtime._memory_contracts import memory_representation_from_dict
from runtime._memory_materialization import MaterializationBatch
from runtime.contracts import UserMessage
from tests.memory_contract._helpers import representation_dict


NOW = datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc)


def open_ledger(tmp_path) -> EventLedger:
    return EventLedger.open(tmp_path / "memory.sqlite3", clock=lambda: NOW)


def append_evidence(
    ledger: EventLedger,
    *,
    request_id: str = "request-1",
    conversation_id: str = "conversation-1",
    text: str = "我下周可能去上海出差，还没完全定。",
) -> EventRecord:
    return ledger.append_user_message(
        UserMessage(
            request_id=request_id,
            conversation_id=conversation_id,
            text=text,
            occurred_at=NOW,
            timezone="Asia/Shanghai",
            source="typed",
        )
    ).event


def memory_for(
    event: EventRecord,
    *,
    memory_id: str = "memory-1",
    proposal_run_id: str = "proposal-run-1",
    proposal_ordinal: int = 0,
    statement: str = "玩家表示下周可能去上海出差，但尚未确定",
    relations: dict[str, object] | None = None,
    created_at: str = "2026-08-07T12:00:00Z",
):
    raw = representation_dict(
        text=event.text,
        event_id=event.event_id,
        conversation_id=event.conversation_id,
    )
    raw.update(
        {
            "memory_id": memory_id,
            "proposal_run_id": proposal_run_id,
            "proposal_ordinal": proposal_ordinal,
            "statement": statement,
            "created_at": created_at,
            "idempotency_key": f"{proposal_run_id}:{proposal_ordinal}",
        }
    )
    if relations is not None:
        raw["relations"] = deepcopy(relations)
    return memory_representation_from_dict(raw)


def batch(*memories, proposal_run_id: str = "proposal-run-1") -> MaterializationBatch:
    return MaterializationBatch(proposal_run_id, tuple(memories))


def begin(store, event: EventRecord, *, proposal_run_id: str = "proposal-run-1", key: str = "run-begin-1"):
    return store.begin_run(
        proposal_run_id=proposal_run_id,
        conversation_id=event.conversation_id,
        from_sequence_no=event.sequence_no,
        through_sequence_no=event.sequence_no,
        idempotency_key=key,
    )
