from __future__ import annotations

import time

import pytest

from runtime import Completed, TextDelta
from runtime._ledger import EventLedger
from runtime.adapters import FakeReplyModel
from tests.memory_scheduler._helpers import (
    RecordingWorker,
    collect,
    message,
    runtime_for,
)


async def test_background_starts_only_after_committed_completed_event_is_delivered(
    tmp_path,
) -> None:
    worker = RecordingWorker()
    async with runtime_for(tmp_path, worker) as runtime:
        stream = runtime.handle_turn(message(1))
        assert isinstance(await anext(stream), TextDelta)
        assert worker.started.is_set() is False
        assert isinstance(await anext(stream), Completed)
        assert worker.started.is_set() is False
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        await worker.started.wait()
        await runtime.wait_for_memory_background_idle()
        snapshot = runtime.memory_background_snapshot()
        assert snapshot.completed == 1
        assert worker.max_active == 1


async def test_new_foreground_turn_cancels_then_resumes_background_in_fifo_order(
    tmp_path,
) -> None:
    worker = RecordingWorker(block_first_attempt=True)
    overlap = []

    async def assert_background_released(_request):
        overlap.append(worker.active)

    model = FakeReplyModel(["收到。"], on_request=assert_background_released)
    async with runtime_for(tmp_path, worker, model=model) as runtime:
        await collect(runtime, message(1))
        await worker.started.wait()

        started = time.perf_counter()
        second = await collect(runtime, message(2))
        foreground_ms = (time.perf_counter() - started) * 1000
        assert isinstance(second[-1], Completed)
        assert foreground_ms < 100.0
        assert worker.cancelled.is_set()

        await runtime.wait_for_memory_background_idle()
        snapshot = runtime.memory_background_snapshot()
        assert snapshot.completed == 2
        assert snapshot.pending == 0
        assert snapshot.cancellation_count == 1
        assert snapshot.foreground_preemption_count >= 1
        assert worker.max_active == 1
        assert overlap == [0, 0]
        assert [job.request_id for job in worker.calls] == [
            "request-1",
            "request-1",
            "request-2",
        ]


async def test_retryable_failure_waits_for_next_wake_and_terminal_failure_stays_terminal(
    tmp_path,
) -> None:
    retrying = RecordingWorker(retryable_failure_once=True)
    async with runtime_for(tmp_path / "retry", retrying) as runtime:
        await collect(runtime, message(1))
        await runtime.wait_for_memory_background_idle()
        assert runtime.memory_background_snapshot().failed_retryable == 1

        await collect(runtime, message(2))
        await runtime.wait_for_memory_background_idle()
        snapshot = runtime.memory_background_snapshot()
        assert snapshot.completed == 2
        assert snapshot.failed_retryable == 0

    terminal = RecordingWorker(terminal_failure=True)
    async with runtime_for(tmp_path / "terminal", terminal) as runtime:
        await collect(runtime, message(1))
        await runtime.wait_for_memory_background_idle()
        assert runtime.memory_background_snapshot().failed_terminal == 1


async def test_runtime_close_cancels_worker_and_leaves_durable_pending_job(tmp_path) -> None:
    worker = RecordingWorker(block_first_attempt=True)
    runtime = runtime_for(tmp_path, worker)
    await runtime.start()
    await collect(runtime, message(1))
    await worker.started.wait()
    await runtime.close()
    assert worker.cancelled.is_set()

    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        assert ledger.memory_background_queue().counts()["pending"] == 1
    finally:
        ledger.close()


async def test_without_worker_does_not_enqueue_background_jobs(tmp_path) -> None:
    from runtime import RelationshipRuntime, RuntimeConfig
    from runtime.adapters import FakeReplyModel

    async with RelationshipRuntime.open(
        RuntimeConfig(tmp_path), FakeReplyModel(["普通回复"])
    ) as runtime:
        result = await collect(runtime, message(1))
        assert isinstance(result[-1], Completed)
        assert runtime.memory_background_snapshot().pending == 0


def test_invalid_background_configuration_releases_database_lock(tmp_path) -> None:
    from runtime import RelationshipRuntime, RuntimeConfig
    from runtime._ledger import EventLedger
    from runtime.adapters import FakeReplyModel

    worker = RecordingWorker()
    with pytest.raises(ValueError, match="not both"):
        RelationshipRuntime.open(
            RuntimeConfig(tmp_path),
            FakeReplyModel(),
            memory_background_worker=worker,
            memory_propose_model=object(),
        )
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    ledger.close()
