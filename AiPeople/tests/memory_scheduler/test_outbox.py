from __future__ import annotations

import pytest

from runtime._ledger import EventLedger
from runtime._memory_scheduler import MemoryBackgroundScheduler
from tests.memory_scheduler._helpers import NOW, RecordingWorker, message


def test_character_commit_and_background_outbox_are_atomic(tmp_path, monkeypatch) -> None:
    ledger = EventLedger.open(tmp_path / "memory.sqlite3", clock=lambda: NOW)
    try:
        user = ledger.append_user_message(message(1)).event

        def fail_enqueue(*args, **kwargs):
            raise RuntimeError("injected outbox failure")

        monkeypatch.setattr(ledger, "_enqueue_memory_background_job", fail_enqueue)
        with pytest.raises(RuntimeError, match="outbox"):
            ledger.append_character_message(
                request_id="request-1",
                conversation_id="memory-scheduler",
                user_event_id=user.event_id,
                text="不会提交",
                enqueue_memory_background=True,
            )
        assert ledger.find_completed_reply("request-1") is None
        assert ledger.memory_background_queue().counts()["pending"] == 0
    finally:
        ledger.close()


def test_outbox_enqueue_is_idempotent_for_lost_reply_response(tmp_path) -> None:
    ledger = EventLedger.open(tmp_path / "memory.sqlite3", clock=lambda: NOW)
    try:
        user = ledger.append_user_message(message(1)).event
        first = ledger.append_character_message(
            request_id="request-1",
            conversation_id="memory-scheduler",
            user_event_id=user.event_id,
            text="已提交",
            enqueue_memory_background=True,
        )
        second = ledger.append_character_message(
            request_id="request-1",
            conversation_id="memory-scheduler",
            user_event_id=user.event_id,
            text="已提交",
            enqueue_memory_background=True,
        )
        queue = ledger.memory_background_queue()
        assert second == first
        assert queue.counts()["pending"] == 1
        job = queue.get(f"memory-propose:{first.event_id}")
        assert job is not None
        assert job.from_sequence_no == user.sequence_no
        assert job.through_sequence_no == first.sequence_no
        assert job.proposal_run_id == job.job_id
    finally:
        ledger.close()


async def test_running_job_is_recovered_after_process_restart(tmp_path) -> None:
    path = tmp_path / "memory.sqlite3"
    ledger = EventLedger.open(path, clock=lambda: NOW)
    user = ledger.append_user_message(message(1)).event
    assistant = ledger.append_character_message(
        request_id="request-1",
        conversation_id="memory-scheduler",
        user_event_id=user.event_id,
        text="已提交",
        enqueue_memory_background=True,
    )
    job_id = f"memory-propose:{assistant.event_id}"
    assert ledger.memory_background_queue().claim_next().state == "running"
    ledger.close()

    reopened = EventLedger.open(path, clock=lambda: NOW)
    worker = RecordingWorker()
    scheduler = MemoryBackgroundScheduler(reopened.memory_background_queue(), worker)
    try:
        await scheduler.start()
        await scheduler.wait_idle()
        assert reopened.memory_background_queue().get(job_id).state == "completed"
        assert worker.attempts[job_id] == 1
    finally:
        await scheduler.close()
        reopened.close()
