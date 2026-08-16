from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from ._context import ReplyContext
from ._memory_selection import MemorySelectionMetrics, MemorySelectionOutcome
from ._model import ReplyModel, ReplyRequest
from ._selected_memory import SelectedMemoryAssembler, SelectedMemoryFrame


LOGGER = logging.getLogger(__name__)


@runtime_checkable
class OnlineMemorySelector(Protocol):
    async def start(self) -> None: ...

    async def select(self, context: ReplyContext) -> MemorySelectionOutcome: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MemorySelectionAudit:
    request_id: str
    status: str
    failure_code: str | None
    metrics: MemorySelectionMetrics | None
    selected_memory_count: int
    selected_memory_tokens: int
    truncated: bool


class MemoryAwareReplyModel:
    """Adds long-term memory before exactly one underlying REPLY generation."""

    def __init__(
        self,
        reply_model: ReplyModel,
        selector: OnlineMemorySelector,
        frame_assembler: SelectedMemoryAssembler,
    ) -> None:
        self._reply_model = reply_model
        self._selector = selector
        self._frame_assembler = frame_assembler
        self.selection_audits: list[MemorySelectionAudit] = []

    @property
    def context_size(self):
        return getattr(self._reply_model, "context_size", None)

    async def start(self) -> None:
        await self._selector.start()
        try:
            start = getattr(self._reply_model, "start", None)
            if start is not None:
                await start()
        except BaseException:
            await self._selector.close()
            raise

    async def close(self) -> None:
        close = getattr(self._reply_model, "close", None)
        try:
            if close is not None:
                await close()
        finally:
            await self._selector.close()

    async def measure_prompt(self, context: ReplyContext) -> int:
        return await self._reply_model.measure_prompt(context)

    async def stream_reply(self, request: ReplyRequest) -> AsyncIterator[str]:
        enriched = request.context
        outcome = None
        frame = SelectedMemoryFrame.empty()
        failure_code = None
        try:
            outcome = await self._selector.select(request.context)
            if outcome.batch is not None and outcome.selected_scores:
                enriched, frame = await self._frame_assembler.fit(
                    request.context,
                    outcome.batch,
                    outcome.selected_scores,
                    selector_version=outcome.selector_version,
                    measure_prompt=self._reply_model.measure_prompt,
                )
            else:
                frame = SelectedMemoryFrame.empty(outcome.selector_version)
                enriched = replace(request.context, selected_memory_frame=frame)
        except asyncio.TimeoutError:
            failure_code = "reranker_timeout"
            enriched = replace(request.context, selected_memory_frame=frame)
        except Exception as error:
            failure_code = "memory_selection_failed"
            enriched = replace(request.context, selected_memory_frame=frame)
            LOGGER.warning(
                "memory_selection_degraded request_id=%s error_type=%s",
                request.request_id,
                type(error).__name__,
            )

        self.selection_audits.append(
            MemorySelectionAudit(
                request_id=request.request_id,
                status=(outcome.status if outcome is not None and failure_code is None else "degraded"),
                failure_code=failure_code,
                metrics=(outcome.metrics if outcome is not None else None),
                selected_memory_count=len(frame.selected_memories),
                selected_memory_tokens=frame.token_count,
                truncated=frame.truncated,
            )
        )
        audit = self.selection_audits[-1]
        LOGGER.info(
            "memory_selection_completed request_id=%s status=%s failure_code=%s "
            "pool_memories=%d pool_views=%d candidates=%d selected=%d "
            "selected_tokens=%d truncated=%s total_ms=%s",
            request.request_id,
            audit.status,
            audit.failure_code or "none",
            outcome.metrics.pool_memory_count if outcome is not None else 0,
            outcome.metrics.pool_view_count if outcome is not None else 0,
            outcome.metrics.candidate_count if outcome is not None else 0,
            audit.selected_memory_count,
            audit.selected_memory_tokens,
            audit.truncated,
            f"{outcome.metrics.total_ms:.3f}" if outcome is not None else "none",
        )
        enriched_request = replace(request, context=enriched)
        async for chunk in self._reply_model.stream_reply(enriched_request):
            yield chunk
