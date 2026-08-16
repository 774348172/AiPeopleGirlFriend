from __future__ import annotations

from dataclasses import replace

import pytest

from runtime._memory_materialization import MaterializationBatch
from runtime._memory_store import (
    MemoryIdempotencyConflictError,
    MemoryRunConflictError,
)
from tests.memory_commit._helpers import append_evidence, batch, begin, memory_for, open_ledger


def test_commit_is_atomic_append_only_and_retry_returns_persisted_result(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        begin(store, event)
        original = memory_for(event)
        first = store.commit_batch(batch(original), idempotency_key="run-commit-1")
        assert first.created is True
        assert first.run.state == "committed"
        assert first.memories == (original,)

        rematerialized = replace(
            original,
            memory_id="memory-new-after-lost-response",
            created_at="2026-08-07T12:05:00Z",
        )
        retried = store.commit_batch(
            batch(rematerialized), idempotency_key="run-commit-retry"
        )
        assert retried.created is False
        assert retried.memories == (original,)

        with pytest.raises(Exception, match="append-only"):
            store._connection.execute("UPDATE memory_transitions SET reason_code='x'")
        with pytest.raises(Exception, match="append-only"):
            store._connection.execute("DELETE FROM memory_run_transitions")
    finally:
        ledger.close()


def test_same_run_rejects_semantically_different_retry(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        begin(store, event)
        original = memory_for(event)
        store.commit_batch(batch(original), idempotency_key="run-commit-1")
        changed = replace(original, statement="玩家已经确定去上海")
        with pytest.raises(MemoryIdempotencyConflictError):
            store.commit_batch(batch(changed), idempotency_key="run-commit-2")
    finally:
        ledger.close()


def test_commit_failure_rolls_back_memories_transitions_and_run_state(tmp_path, monkeypatch) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        begin(store, event)
        memory = memory_for(event)

        def fail_projection(*args, **kwargs):
            raise RuntimeError("injected projection failure")

        monkeypatch.setattr(store, "_write_projection", fail_projection)
        with pytest.raises(RuntimeError, match="injected"):
            store.commit_batch(batch(memory), idempotency_key="run-commit-1")
        assert store._require_run("proposal-run-1").state == "pending"
        assert store.list_memories() == ()
        assert store._connection.execute("SELECT COUNT(*) FROM memory_transitions").fetchone()[0] == 0
        assert store._connection.execute("SELECT COUNT(*) FROM memory_run_transitions").fetchone()[0] == 1
    finally:
        ledger.close()


def test_empty_batch_commits_run_without_placeholder_memory(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        begin(store, event)
        result = store.commit_batch(
            MaterializationBatch("proposal-run-1", ()),
            idempotency_key="run-commit-empty",
        )
        assert result.run.state == "committed"
        assert result.memories == ()
    finally:
        ledger.close()


def test_retryable_failure_opens_new_attempt_but_terminal_failure_does_not(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        begin(store, event)
        failed = store.record_failure(
            proposal_run_id="proposal-run-1",
            failure_code="memory_propose_timeout",
            retryable=True,
            idempotency_key="run-failure-1",
        )
        assert failed.state == "failed_retryable"
        retried = begin(store, event, key="run-begin-2")
        assert retried.state == "pending"
        assert retried.attempt == 2

        store.record_failure(
            proposal_run_id="proposal-run-1",
            failure_code="memory_propose_invalid_json",
            retryable=False,
            idempotency_key="run-failure-2",
        )
        with pytest.raises(MemoryRunConflictError, match="terminal"):
            begin(store, event, key="run-begin-3")
    finally:
        ledger.close()


def test_run_and_transition_idempotency_keys_cannot_be_reused(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        begin(store, event, key="shared-key")
        with pytest.raises(MemoryIdempotencyConflictError):
            store.begin_run(
                proposal_run_id="proposal-run-other",
                conversation_id=event.conversation_id,
                from_sequence_no=event.sequence_no,
                through_sequence_no=event.sequence_no,
                idempotency_key="shared-key",
            )
    finally:
        ledger.close()
