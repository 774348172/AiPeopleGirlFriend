from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone

from runtime import RelationshipRuntime, RuntimeConfig, UserMessage
from runtime._memory_scheduler import BackgroundJobFailure
from runtime.adapters import FakeReplyModel


NOW = datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc)


def message(index: int, *, conversation_id: str = "memory-scheduler") -> UserMessage:
    return UserMessage(
        request_id=f"request-{index}",
        conversation_id=conversation_id,
        text=f"第{index}条消息",
        occurred_at=NOW,
        timezone="Asia/Shanghai",
    )


async def collect(runtime: RelationshipRuntime, value: UserMessage):
    return [event async for event in runtime.handle_turn(value)]


class RecordingWorker:
    def __init__(
        self,
        *,
        block_first_attempt: bool = False,
        retryable_failure_once: bool = False,
        terminal_failure: bool = False,
    ) -> None:
        self.block_first_attempt = block_first_attempt
        self.retryable_failure_once = retryable_failure_once
        self.terminal_failure = terminal_failure
        self.calls = []
        self.attempts: Counter[str] = Counter()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self._never = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def run_memory_propose(self, job) -> None:
        self.calls.append(job)
        self.attempts[job.job_id] += 1
        attempt = self.attempts[job.job_id]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.terminal_failure:
                raise BackgroundJobFailure(
                    "memory_propose_invalid_json", retryable=False
                )
            if self.retryable_failure_once and len(self.calls) == 1:
                raise BackgroundJobFailure("memory_propose_timeout", retryable=True)
            if self.block_first_attempt and len(self.calls) == 1:
                try:
                    await self._never.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
        finally:
            self.active -= 1


def runtime_for(tmp_path, worker, *, model=None):
    return RelationshipRuntime.open(
        RuntimeConfig(tmp_path),
        model or FakeReplyModel(["收到。"]),
        clock=lambda: NOW,
        memory_background_worker=worker,
    )
