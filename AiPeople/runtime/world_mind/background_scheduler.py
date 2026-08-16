from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from .contracts import RuntimeSessionIdentity
from .game_clock import GameClockService
from .memory_worker import R1MemoryExecutionError, R1MemoryWorker
from .model_failures import (
    MODEL_CANCELLED,
    MODEL_EMPTY_OUTPUT,
    MODEL_SERVICE_UNAVAILABLE,
    MODEL_TIMEOUT,
    classify_model_failure,
)
from .persistence import R1MemoryJob, ReconcileJob, WorldMindStore
from .reconciliation import ReconcileExecutionError, WorldMindReconcileWorker


@dataclass(slots=True)
class _SchedulerSlot:
    session: RuntimeSessionIdentity
    execution_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    runner_task: asyncio.Task[None] | None = None
    periodic_task: asyncio.Task[None] | None = None
    running_job: ReconcileJob | R1MemoryJob | None = None
    foreground_active: bool = False
    foreground_waiters: int = 0


@dataclass(slots=True)
class SchedulerMetrics:
    foreground_preemptions: int = 0
    jobs_started: int = 0
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_requeued: int = 0
    peak_pending_jobs: int = 0
    backpressure_waits: int = 0


class WorldBackgroundScheduler:
    def __init__(
        self,
        *,
        store: WorldMindStore,
        game_clock: GameClockService,
        worker: WorldMindReconcileWorker,
        memory_worker: R1MemoryWorker | None = None,
        periodic_interval_seconds: float = 300.0,
        required_before_foreground: bool = True,
        coalesce_pending_required: bool = True,
        queue_limit: int = 16,
        retry_base_delay_seconds: float = 1.0,
        retry_max_delay_seconds: float = 30.0,
    ) -> None:
        if periodic_interval_seconds <= 0:
            raise ValueError("periodic_interval_seconds must be positive")
        if type(queue_limit) is not int or queue_limit <= 0:
            raise ValueError("queue_limit must be a positive integer")
        if retry_base_delay_seconds <= 0 or retry_max_delay_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if retry_base_delay_seconds > retry_max_delay_seconds:
            raise ValueError("retry base delay cannot exceed retry max delay")
        self.store = store
        self.game_clock = game_clock
        self.worker = worker
        self.memory_worker = memory_worker
        self.periodic_interval_seconds = periodic_interval_seconds
        self.required_before_foreground = required_before_foreground
        self.coalesce_pending_required = coalesce_pending_required
        self.queue_limit = queue_limit
        self.retry_base_delay_seconds = retry_base_delay_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self._slots: dict[tuple[str, str], _SchedulerSlot] = {}
        self._state_lock = asyncio.Lock()
        self._started = False
        self._closed = False
        self.metrics = SchedulerMetrics()

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("world background scheduler is closed")
        if self._started:
            return
        self.store.recover_reconcile_jobs()
        if self.memory_worker is not None:
            self.store.recover_r1_memory_jobs()
        self._started = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._started = False
        async with self._state_lock:
            tasks = [
                task
                for slot in self._slots.values()
                for task in (slot.runner_task, slot.periodic_task)
                if task is not None and not task.done()
            ]
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def activate_session(self, session: RuntimeSessionIdentity) -> None:
        if not self._started or self._closed:
            raise RuntimeError("world background scheduler is not running")
        key = _slot_key(session)
        async with self._state_lock:
            slot = self._slots.get(key)
            if slot is None:
                slot = _SchedulerSlot(session=session)
                self._slots[key] = slot
            elif slot.session != session:
                if (
                    slot.session.world_id != session.world_id
                    or slot.session.protagonist_id != session.protagonist_id
                ):
                    raise RuntimeError("scheduler session identity changed")
                slot.session = session
            if slot.periodic_task is None or slot.periodic_task.done():
                slot.periodic_task = asyncio.create_task(
                    self._periodic_loop(slot),
                    name=f"world-mind-periodic:{session.save_id}",
                )
        await self._ensure_runner(slot)

    @asynccontextmanager
    async def foreground(
        self,
        session: RuntimeSessionIdentity,
    ) -> AsyncIterator[None]:
        await self.activate_session(session)
        slot = self._slots[_slot_key(session)]
        async with self._state_lock:
            slot.foreground_waiters += 1
            runner = slot.runner_task
            if runner is not None and not runner.done():
                self.metrics.foreground_preemptions += 1
                runner.cancel()
        if runner is not None and not runner.done():
            await asyncio.gather(runner, return_exceptions=True)
        async with slot.execution_lock:
            async with self._state_lock:
                slot.foreground_waiters -= 1
                slot.foreground_active = True
            try:
                if self.required_before_foreground:
                    await self._run_required(slot)
                yield
            finally:
                async with self._state_lock:
                    slot.foreground_active = False
        if self.coalesce_pending_required:
            self.store.supersede_older_pending_required_jobs(session)
        await self._ensure_runner(slot)

    async def trigger_periodic(
        self,
        session: RuntimeSessionIdentity,
    ) -> ReconcileJob:
        await self.activate_session(session)
        job = self.store.enqueue_periodic_reconcile(
            session,
            self.game_clock.current_time(session),
            interval_seconds=self.periodic_interval_seconds,
        )
        self._observe_queue(session)
        await self._ensure_runner(self._slots[_slot_key(session)])
        return job

    async def wait_idle(
        self,
        session: RuntimeSessionIdentity,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        async with asyncio.timeout(timeout_seconds):
            while True:
                slot = self._slots.get(_slot_key(session))
                runner_done = (
                    slot is None
                    or slot.runner_task is None
                    or slot.runner_task.done()
                )
                if runner_done and not self._has_pending(session):
                    return
                await asyncio.sleep(0.001)

    async def _periodic_loop(self, slot: _SchedulerSlot) -> None:
        while True:
            await asyncio.sleep(self.periodic_interval_seconds)
            await self.trigger_periodic(slot.session)

    async def _ensure_runner(self, slot: _SchedulerSlot) -> None:
        self._observe_queue(slot.session)
        async with self._state_lock:
            if (
                self._closed
                or slot.foreground_active
                or slot.foreground_waiters > 0
                or (slot.runner_task is not None and not slot.runner_task.done())
                or not self._has_pending(slot.session)
            ):
                return
            slot.runner_task = asyncio.create_task(
                self._run_pending(slot),
                name=f"world-mind-background:{slot.session.save_id}",
            )

    async def _run_pending(self, slot: _SchedulerSlot) -> None:
        try:
            async with slot.execution_lock:
                while True:
                    async with self._state_lock:
                        if self._closed or slot.foreground_waiters > 0:
                            return
                    job = self.store.claim_next_reconcile_job(
                        slot.session,
                        required_only=True,
                    )
                    if job is None and self.memory_worker is not None:
                        job = self.store.claim_next_r1_memory_job(slot.session)
                    if job is None:
                        job = self.store.claim_next_reconcile_job(slot.session)
                    if job is None:
                        delay = self._next_ready_delay(slot.session)
                        if delay is None:
                            return
                        self.metrics.backpressure_waits += 1
                        await asyncio.sleep(max(0.01, delay))
                        continue
                    await self._execute_job(slot, job)
        finally:
            async with self._state_lock:
                if slot.runner_task is asyncio.current_task():
                    slot.runner_task = None
            if not self._closed:
                await self._ensure_runner(slot)

    async def _run_required(self, slot: _SchedulerSlot) -> None:
        while True:
            job = self.store.claim_next_reconcile_job(
                slot.session,
                required_only=True,
            )
            if job is None:
                return
            await self._execute_job(slot, job)

    async def _execute_job(
        self,
        slot: _SchedulerSlot,
        job: ReconcileJob | R1MemoryJob,
    ) -> None:
        slot.running_job = job
        self.metrics.jobs_started += 1
        try:
            if isinstance(job, ReconcileJob):
                await self.worker.run_reconcile_job(job)
            else:
                if self.memory_worker is None:
                    raise RuntimeError("R1 memory worker is not configured")
                await self.memory_worker.run_memory_job(job)
            self.metrics.jobs_completed += 1
        except asyncio.CancelledError:
            if isinstance(job, ReconcileJob):
                self.store.requeue_reconcile_job(
                    job.job_id,
                    "foreground_preempted",
                    retry_delay_seconds=0.25,
                )
            else:
                self.store.requeue_r1_memory_job(
                    job.job_id,
                    "foreground_preempted",
                    retry_delay_seconds=0.25,
                )
            self.metrics.jobs_requeued += 1
            raise
        except ReconcileExecutionError as error:
            self.store.fail_reconcile_job(
                job,
                error.code,
                retryable=error.retryable,
                retry_delay_seconds=self._retry_delay(job.attempt),
            )
            self.metrics.jobs_failed += 1
        except R1MemoryExecutionError as error:
            assert isinstance(job, R1MemoryJob)
            self.store.fail_r1_memory_job(
                job,
                error.code,
                retryable=error.retryable,
                retry_delay_seconds=self._retry_delay(job.attempt),
            )
            self.metrics.jobs_failed += 1
        except Exception as error:
            code = classify_model_failure(error)
            retryable = _retryable_background_failure(code)
            if isinstance(job, ReconcileJob):
                self.store.fail_reconcile_job(
                    job,
                    code,
                    retryable=retryable,
                    retry_delay_seconds=self._retry_delay(job.attempt),
                )
            else:
                self.store.fail_r1_memory_job(
                    job,
                    code,
                    retryable=retryable,
                    retry_delay_seconds=self._retry_delay(job.attempt),
                )
            self.metrics.jobs_failed += 1
        finally:
            slot.running_job = None
            self._observe_queue(slot.session)

    def snapshot_metrics(self, session: RuntimeSessionIdentity) -> dict[str, int]:
        self._observe_queue(session)
        return {
            "foreground_preemptions": self.metrics.foreground_preemptions,
            "jobs_started": self.metrics.jobs_started,
            "jobs_completed": self.metrics.jobs_completed,
            "jobs_failed": self.metrics.jobs_failed,
            "jobs_requeued": self.metrics.jobs_requeued,
            "peak_pending_jobs": self.metrics.peak_pending_jobs,
            "backpressure_waits": self.metrics.backpressure_waits,
        }

    def _observe_queue(self, session: RuntimeSessionIdentity) -> None:
        pending = self.store.reconcile_queue_snapshot(session.save_id)["pending"]
        if self.memory_worker is not None:
            pending += self.store.r1_memory_queue_snapshot(session.save_id)["pending"]
        if pending >= self.queue_limit and self.coalesce_pending_required:
            self.store.supersede_older_pending_required_jobs(session)
            pending = self.store.reconcile_queue_snapshot(session.save_id)["pending"]
            if self.memory_worker is not None:
                pending += self.store.r1_memory_queue_snapshot(session.save_id)[
                    "pending"
                ]
        self.metrics.peak_pending_jobs = max(
            self.metrics.peak_pending_jobs,
            pending,
        )

    def _has_pending(self, session: RuntimeSessionIdentity) -> bool:
        return self.store.has_pending_reconcile_jobs(session) or (
            self.memory_worker is not None
            and self.store.has_pending_r1_memory_jobs(session)
        )

    def _next_ready_delay(self, session: RuntimeSessionIdentity) -> float | None:
        delays = [
            self.store.next_reconcile_ready_delay(session),
            (
                self.store.next_r1_memory_ready_delay(session)
                if self.memory_worker is not None
                else None
            ),
        ]
        available = [delay for delay in delays if delay is not None]
        return min(available) if available else None

    def _retry_delay(self, attempt: int) -> float:
        exponent = max(0, attempt - 1)
        return min(
            self.retry_max_delay_seconds,
            self.retry_base_delay_seconds * (2**exponent),
        )


def _slot_key(session: RuntimeSessionIdentity) -> tuple[str, str]:
    return session.save_id, session.active_character_id


def _retryable_background_failure(code: str) -> bool:
    return code in {
        MODEL_CANCELLED,
        MODEL_EMPTY_OUTPUT,
        MODEL_SERVICE_UNAVAILABLE,
        MODEL_TIMEOUT,
        "model_unknown_failure",
    }
