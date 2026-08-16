from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


LOGGER = logging.getLogger(__name__)
MEMORY_BACKGROUND_MODE = "MEMORY_PROPOSE"
BACKGROUND_JOB_STATES = frozenset(
    {"pending", "running", "failed_retryable", "failed_terminal", "completed"}
)


class MemoryBackgroundError(RuntimeError):
    pass


class BackgroundJobFailure(MemoryBackgroundError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("background failure code must not be empty")
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class MemoryBackgroundJob:
    job_id: str
    queue_ordinal: int
    proposal_run_id: str
    request_id: str
    conversation_id: str
    user_event_id: str
    assistant_event_id: str
    from_sequence_no: int
    through_sequence_no: int
    state: str
    attempt: int
    failure_code: str | None


class MemoryBackgroundWorker(Protocol):
    async def run_memory_propose(self, job: MemoryBackgroundJob) -> None: ...


@dataclass(frozen=True, slots=True)
class BackgroundSchedulerSnapshot:
    pending: int
    running: int
    failed_retryable: int
    failed_terminal: int
    completed: int
    running_job_id: str | None
    cancellation_count: int
    foreground_preemption_count: int


class MemoryBackgroundQueue:
    def __init__(
        self,
        connection: sqlite3.Connection,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._connection = connection
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def recover_interrupted(self) -> int:
        cursor = self._connection.execute(
            """
            UPDATE memory_background_jobs
            SET state='pending', failure_code=NULL, updated_at=?
            WHERE state='running'
            """,
            (self._now(),),
        )
        return cursor.rowcount

    def retry_failed(self) -> int:
        cursor = self._connection.execute(
            """
            UPDATE memory_background_jobs
            SET state='pending', failure_code=NULL, updated_at=?
            WHERE state='failed_retryable'
            """,
            (self._now(),),
        )
        return cursor.rowcount

    def claim_next(self) -> MemoryBackgroundJob | None:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """
                SELECT * FROM memory_background_jobs
                WHERE state='pending'
                ORDER BY queue_ordinal
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE memory_background_jobs
                SET state='running', attempt=attempt+1, failure_code=NULL,
                    updated_at=?
                WHERE job_id=? AND state='pending'
                """,
                (self._now(), str(row["job_id"])),
            )
            claimed = connection.execute(
                "SELECT * FROM memory_background_jobs WHERE job_id=?",
                (str(row["job_id"]),),
            ).fetchone()
            connection.commit()
            return _job_from_row(claimed)
        except Exception:
            connection.rollback()
            raise

    def mark_completed(self, job_id: str) -> None:
        self._transition(job_id, "running", "completed", None)

    def mark_cancelled(self, job_id: str) -> None:
        self._transition(job_id, "running", "pending", None)

    def mark_failed(self, job_id: str, code: str, *, retryable: bool) -> None:
        self._transition(
            job_id,
            "running",
            "failed_retryable" if retryable else "failed_terminal",
            code,
        )

    def counts(self) -> dict[str, int]:
        result = {state: 0 for state in BACKGROUND_JOB_STATES}
        rows = self._connection.execute(
            "SELECT state, COUNT(*) AS total FROM memory_background_jobs GROUP BY state"
        ).fetchall()
        for row in rows:
            result[str(row["state"])] = int(row["total"])
        return result

    def get(self, job_id: str) -> MemoryBackgroundJob | None:
        row = self._connection.execute(
            "SELECT * FROM memory_background_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        return _job_from_row(row) if row is not None else None

    def _transition(
        self, job_id: str, expected: str, state: str, failure_code: str | None
    ) -> None:
        cursor = self._connection.execute(
            """
            UPDATE memory_background_jobs
            SET state=?, failure_code=?, updated_at=?
            WHERE job_id=? AND state=?
            """,
            (state, failure_code, self._now(), job_id, expected),
        )
        if cursor.rowcount != 1:
            raise MemoryBackgroundError(
                f"background job {job_id} is not in {expected} state"
            )

    def _now(self) -> str:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("background queue clock must return an aware datetime")
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )


class MemoryBackgroundScheduler:
    def __init__(
        self,
        queue: MemoryBackgroundQueue,
        worker: MemoryBackgroundWorker | None,
    ) -> None:
        self._queue = queue
        self._worker = worker
        self._state_guard = asyncio.Lock()
        self._runner_task: asyncio.Task[None] | None = None
        self._running_job_id: str | None = None
        self._foreground_waiters = 0
        self._foreground_active = 0
        self._closed = False
        self._cancellation_count = 0
        self._foreground_preemption_count = 0
        self._idle = asyncio.Event()
        self._idle.set()

    async def start(self) -> None:
        self._queue.recover_interrupted()
        self._queue.retry_failed()
        async with self._state_guard:
            self._ensure_runner_locked()

    async def close(self) -> None:
        async with self._state_guard:
            if self._closed:
                return
            self._closed = True
            task = self._runner_task
            if task is not None and not task.done():
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._idle.set()

    async def notify_new_job(self) -> None:
        self._queue.retry_failed()
        async with self._state_guard:
            self._ensure_runner_locked()

    @asynccontextmanager
    async def foreground(self) -> AsyncIterator[None]:
        task: asyncio.Task[None] | None
        async with self._state_guard:
            if self._closed:
                raise RuntimeError("background scheduler is closed")
            self._foreground_waiters += 1
            task = self._runner_task
            if task is not None and not task.done():
                self._foreground_preemption_count += 1
                task.cancel()
        try:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        except BaseException:
            async with self._state_guard:
                self._foreground_waiters -= 1
                self._ensure_runner_locked()
            raise

        async with self._state_guard:
            self._foreground_waiters -= 1
            self._foreground_active += 1
        try:
            yield
        finally:
            async with self._state_guard:
                self._foreground_active -= 1
                self._ensure_runner_locked()

    async def wait_idle(self, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        await asyncio.wait_for(self._idle.wait(), timeout_seconds)

    def snapshot(self) -> BackgroundSchedulerSnapshot:
        counts = self._queue.counts()
        return BackgroundSchedulerSnapshot(
            pending=counts["pending"],
            running=counts["running"],
            failed_retryable=counts["failed_retryable"],
            failed_terminal=counts["failed_terminal"],
            completed=counts["completed"],
            running_job_id=self._running_job_id,
            cancellation_count=self._cancellation_count,
            foreground_preemption_count=self._foreground_preemption_count,
        )

    def _ensure_runner_locked(self) -> None:
        if (
            self._closed
            or self._worker is None
            or self._foreground_waiters > 0
            or self._foreground_active > 0
            or (self._runner_task is not None and not self._runner_task.done())
        ):
            return
        if self._queue.counts()["pending"] == 0:
            self._idle.set()
            return
        self._idle.clear()
        task = asyncio.create_task(self._run_jobs(), name="memory-background-worker")
        self._runner_task = task
        task.add_done_callback(self._runner_finished)

    async def _run_jobs(self) -> None:
        while True:
            async with self._state_guard:
                if (
                    self._closed
                    or self._foreground_waiters > 0
                    or self._foreground_active > 0
                ):
                    return
            job = self._queue.claim_next()
            if job is None:
                return
            self._running_job_id = job.job_id
            started = time.perf_counter()
            try:
                if self._worker is None:
                    raise MemoryBackgroundError("background worker is unavailable")
                await self._worker.run_memory_propose(job)
                self._queue.mark_completed(job.job_id)
                LOGGER.info(
                    "memory_background_completed job_id=%s attempt=%d total_ms=%.3f",
                    job.job_id,
                    job.attempt,
                    (time.perf_counter() - started) * 1000,
                )
            except asyncio.CancelledError:
                self._queue.mark_cancelled(job.job_id)
                self._cancellation_count += 1
                LOGGER.info(
                    "memory_background_cancelled job_id=%s attempt=%d",
                    job.job_id,
                    job.attempt,
                )
                raise
            except BackgroundJobFailure as error:
                self._queue.mark_failed(
                    job.job_id, error.code, retryable=error.retryable
                )
                LOGGER.warning(
                    "memory_background_failed job_id=%s attempt=%d code=%s retryable=%s",
                    job.job_id,
                    job.attempt,
                    error.code,
                    error.retryable,
                )
            except Exception as error:
                self._queue.mark_failed(
                    job.job_id,
                    "memory_propose_runtime_error",
                    retryable=True,
                )
                LOGGER.warning(
                    "memory_background_failed job_id=%s attempt=%d code=memory_propose_runtime_error error_type=%s",
                    job.job_id,
                    job.attempt,
                    type(error).__name__,
                )
            finally:
                self._running_job_id = None

    def _runner_finished(self, task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass
        asyncio.create_task(self._settle_runner(task))

    async def _settle_runner(self, task: asyncio.Task[None]) -> None:
        async with self._state_guard:
            if self._runner_task is task:
                self._runner_task = None
            self._ensure_runner_locked()


def _job_from_row(row: sqlite3.Row) -> MemoryBackgroundJob:
    return MemoryBackgroundJob(
        job_id=str(row["job_id"]),
        queue_ordinal=int(row["queue_ordinal"]),
        proposal_run_id=str(row["proposal_run_id"]),
        request_id=str(row["request_id"]),
        conversation_id=str(row["conversation_id"]),
        user_event_id=str(row["user_event_id"]),
        assistant_event_id=str(row["assistant_event_id"]),
        from_sequence_no=int(row["from_sequence_no"]),
        through_sequence_no=int(row["through_sequence_no"]),
        state=str(row["state"]),
        attempt=int(row["attempt"]),
        failure_code=(
            str(row["failure_code"]) if row["failure_code"] is not None else None
        ),
    )
