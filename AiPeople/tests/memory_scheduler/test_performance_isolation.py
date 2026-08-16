from __future__ import annotations

import asyncio
import math
import time
from collections import Counter

from runtime import TextDelta
from tests.memory_scheduler._helpers import message, runtime_for


class BlockingEachNewJobWorker:
    def __init__(self) -> None:
        self.attempts = Counter()
        self.started = asyncio.Event()
        self._never = asyncio.Event()

    async def run_memory_propose(self, job) -> None:
        self.attempts[job.job_id] += 1
        if self.attempts[job.job_id] == 1:
            self.started.set()
            await self._never.wait()


async def test_repeated_background_preemption_keeps_foreground_first_delta_p95_bounded(
    tmp_path,
) -> None:
    worker = BlockingEachNewJobWorker()
    runtime = runtime_for(tmp_path, worker)
    await runtime.start()
    try:
        first = runtime.handle_turn(message(0))
        assert isinstance(await anext(first), TextDelta)
        async for _ in first:
            pass

        samples = []
        for index in range(1, 21):
            await asyncio.wait_for(worker.started.wait(), 1.0)
            worker.started.clear()
            stream = runtime.handle_turn(message(index))
            started = time.perf_counter()
            assert isinstance(await anext(stream), TextDelta)
            samples.append((time.perf_counter() - started) * 1000)
            async for _ in stream:
                pass

        ordered = sorted(samples)
        p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
        assert p95 < 100.0
        assert runtime.memory_background_snapshot().cancellation_count == 20
    finally:
        await runtime.close()
