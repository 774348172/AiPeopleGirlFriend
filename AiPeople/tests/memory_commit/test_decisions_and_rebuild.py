from __future__ import annotations

import pytest

from runtime._memory_store import MemoryDecisionError, MemoryIdempotencyConflictError
from tests.memory_commit._helpers import append_evidence, batch, begin, memory_for, open_ledger


def _commit_one(store, event, memory, *, run_id, begin_key, commit_key):
    begin(store, event, proposal_run_id=run_id, key=begin_key)
    return store.commit_batch(
        batch(memory, proposal_run_id=run_id), idempotency_key=commit_key
    ).memories[0]


def test_explicit_accept_reject_and_decision_idempotency(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        memory = _commit_one(
            store,
            event,
            memory_for(event),
            run_id="proposal-run-1",
            begin_key="begin-1",
            commit_key="commit-1",
        )
        active = store.decide_memory(
            memory_id=memory.memory_id,
            to_status="active",
            reason_code="evidence_validated",
            idempotency_key="decision-1",
            source_event_id=event.event_id,
        )
        assert active.status == "active"
        assert active.version == 2
        assert store.decide_memory(
            memory_id=memory.memory_id,
            to_status="active",
            reason_code="evidence_validated",
            idempotency_key="decision-1",
            source_event_id=event.event_id,
        ) == active
        with pytest.raises(MemoryIdempotencyConflictError):
            store.decide_memory(
                memory_id=memory.memory_id,
                to_status="disputed",
                reason_code="conflict_detected",
                idempotency_key="decision-1",
            )
    finally:
        ledger.close()


def test_activating_correction_supersedes_old_memory_in_same_transaction(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        old = _commit_one(
            store,
            event,
            memory_for(event, memory_id="memory-old", proposal_run_id="run-old"),
            run_id="run-old",
            begin_key="begin-old",
            commit_key="commit-old",
        )
        store.decide_memory(
            memory_id=old.memory_id,
            to_status="active",
            reason_code="evidence_validated",
            idempotency_key="activate-old",
        )
        correction = memory_for(
            event,
            memory_id="memory-correction",
            proposal_run_id="run-correction",
            statement="玩家修正为下周不去上海",
            relations={
                "semantic_slot": "player.future.travel.shanghai",
                "supersedes": ["memory-old"],
                "contradicts": [],
                "refines": [],
            },
        )
        _commit_one(
            store,
            event,
            correction,
            run_id="run-correction",
            begin_key="begin-correction",
            commit_key="commit-correction",
        )
        current = store.decide_memory(
            memory_id="memory-correction",
            to_status="active",
            reason_code="player_corrected",
            idempotency_key="activate-correction",
            source_event_id=event.event_id,
        )
        assert current.status == "active"
        assert store.get_memory("memory-old").status == "superseded"
    finally:
        ledger.close()


def test_supersedes_failure_rolls_back_both_current_and_target(tmp_path, monkeypatch) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        old = _commit_one(
            store,
            event,
            memory_for(event, memory_id="memory-old", proposal_run_id="run-old"),
            run_id="run-old",
            begin_key="begin-old",
            commit_key="commit-old",
        )
        store.decide_memory(
            memory_id=old.memory_id,
            to_status="active",
            reason_code="evidence_validated",
            idempotency_key="activate-old",
        )
        correction = memory_for(
            event,
            memory_id="memory-correction",
            proposal_run_id="run-correction",
            relations={
                "semantic_slot": "player.future.travel.shanghai",
                "supersedes": ["memory-old"],
                "contradicts": [],
                "refines": [],
            },
        )
        _commit_one(
            store,
            event,
            correction,
            run_id="run-correction",
            begin_key="begin-correction",
            commit_key="commit-correction",
        )
        original_write = store._write_projection
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected supersedes failure")
            return original_write(*args, **kwargs)

        monkeypatch.setattr(store, "_write_projection", fail_second)
        with pytest.raises(RuntimeError, match="injected"):
            store.decide_memory(
                memory_id="memory-correction",
                to_status="active",
                reason_code="player_corrected",
                idempotency_key="activate-correction",
            )
        assert store.get_memory("memory-correction").status == "proposed"
        assert store.get_memory("memory-old").status == "active"
    finally:
        ledger.close()


def test_contradiction_cannot_be_activated_without_resolution(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        old = _commit_one(
            store,
            event,
            memory_for(event, memory_id="memory-old", proposal_run_id="run-old"),
            run_id="run-old",
            begin_key="begin-old",
            commit_key="commit-old",
        )
        store.decide_memory(
            memory_id=old.memory_id,
            to_status="active",
            reason_code="evidence_validated",
            idempotency_key="activate-old",
        )
        conflict = memory_for(
            event,
            memory_id="memory-conflict",
            proposal_run_id="run-conflict",
            relations={
                "semantic_slot": "player.future.travel.shanghai",
                "supersedes": [],
                "contradicts": ["memory-old"],
                "refines": [],
            },
        )
        _commit_one(
            store,
            event,
            conflict,
            run_id="run-conflict",
            begin_key="begin-conflict",
            commit_key="commit-conflict",
        )
        with pytest.raises(MemoryDecisionError, match="disputed"):
            store.decide_memory(
                memory_id="memory-conflict",
                to_status="active",
                reason_code="evidence_validated",
                idempotency_key="activate-conflict",
            )
        disputed = store.decide_memory(
            memory_id="memory-conflict",
            to_status="disputed",
            reason_code="conflict_detected",
            idempotency_key="dispute-conflict",
        )
        assert disputed.status == "disputed"
    finally:
        ledger.close()


def test_all_projections_rebuild_from_append_only_transitions(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event = append_evidence(ledger)
        store = ledger.memory_store()
        memory = _commit_one(
            store,
            event,
            memory_for(event),
            run_id="proposal-run-1",
            begin_key="begin-1",
            commit_key="commit-1",
        )
        store.decide_memory(
            memory_id=memory.memory_id,
            to_status="active",
            reason_code="evidence_validated",
            idempotency_key="activate-1",
        )
        expected = store.list_memories()
        store._connection.execute("DELETE FROM memories")
        store._connection.execute("DELETE FROM memory_proposal_runs")
        rebuilt = store.rebuild_projection()
        assert rebuilt == expected
        assert store._require_run("proposal-run-1").state == "committed"
    finally:
        ledger.close()
