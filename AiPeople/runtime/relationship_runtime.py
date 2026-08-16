from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from ._context import ContextAssembler, ContextBudgetExceeded
from ._ledger import (
    Clock,
    EventLedger,
    IdFactory,
    RequestConflictError,
    _event_id,
    _utc_now,
)
from ._model import ReplyModel, ReplyRequest
from ._memory_scheduler import (
    BackgroundSchedulerSnapshot,
    MemoryBackgroundScheduler,
    MemoryBackgroundWorker,
)
from ._memory_pipeline import MemoryProposalPipeline, MemoryProposeModel
from ._settings import RuntimeConfig
from .contracts import (
    Completed,
    Failed,
    ReplyEvent,
    TextDelta,
    TurnMetrics,
    UserMessage,
    _validation_error,
)


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _ConversationSlot:
    lock: asyncio.Lock
    users: int = 0


class RelationshipRuntime:
    def __init__(
        self,
        config: RuntimeConfig,
        reply_model: ReplyModel,
        ledger: EventLedger,
        clock: Clock,
        memory_background_worker: MemoryBackgroundWorker | None = None,
        memory_propose_model: MemoryProposeModel | None = None,
    ) -> None:
        self._config = config
        self._reply_model = reply_model
        self._ledger = ledger
        self._clock = clock
        if memory_background_worker is not None and memory_propose_model is not None:
            raise ValueError(
                "provide memory_background_worker or memory_propose_model, not both"
            )
        worker = memory_background_worker
        if memory_propose_model is not None:
            worker = MemoryProposalPipeline(ledger, memory_propose_model)
        self._memory_scheduler = MemoryBackgroundScheduler(
            ledger.memory_background_queue(), worker
        )
        self._memory_background_enabled = worker is not None
        self._context_assembler = ContextAssembler(
            ledger=ledger,
            context_size=config.context_size,
            reply_reserve_tokens=config.reply_reserve_tokens,
            safety_margin_tokens=config.context_safety_margin_tokens,
            recall_candidate_limit=config.recall_candidate_limit,
            recall_evidence_limit=config.recall_evidence_limit,
            recall_excerpt_chars=config.recall_excerpt_chars,
            recall_target_tokens=config.recall_target_tokens,
        )
        self._active_requests: set[str] = set()
        self._active_guard = asyncio.Lock()
        self._conversation_slots: dict[str, _ConversationSlot] = {}
        self._conversation_slots_guard = asyncio.Lock()
        self._lifecycle_guard = asyncio.Lock()
        self._started = False
        self._closed = False

    @classmethod
    def open(
        cls,
        config: RuntimeConfig,
        reply_model: ReplyModel,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _event_id,
        memory_background_worker: MemoryBackgroundWorker | None = None,
        memory_propose_model: MemoryProposeModel | None = None,
    ) -> RelationshipRuntime:
        ledger = EventLedger.open(
            config.database_path,
            clock=clock,
            id_factory=id_factory,
        )
        try:
            return cls(
                config,
                reply_model,
                ledger,
                clock,
                memory_background_worker=memory_background_worker,
                memory_propose_model=memory_propose_model,
            )
        except BaseException:
            ledger.close()
            raise

    async def __aenter__(self) -> RelationshipRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def start(self) -> None:
        async with self._lifecycle_guard:
            if self._closed:
                raise RuntimeError("runtime is closed")
            if self._started:
                return
            model_context_size = getattr(self._reply_model, "context_size", None)
            if (
                model_context_size is not None
                and model_context_size != self._config.context_size
            ):
                self._ledger.close()
                self._closed = True
                raise ValueError(
                    "runtime context_size must match reply model context_size"
                )
            start_model = getattr(self._reply_model, "start", None)
            if start_model is not None:
                try:
                    await start_model()
                except BaseException:
                    self._ledger.close()
                    self._closed = True
                    raise
            try:
                await self._memory_scheduler.start()
            except BaseException:
                close_model = getattr(self._reply_model, "close", None)
                try:
                    if close_model is not None:
                        await close_model()
                finally:
                    self._ledger.close()
                    self._closed = True
                raise
            self._started = True

    async def close(self) -> None:
        async with self._lifecycle_guard:
            if self._closed:
                return
            self._closed = True
            close_model = getattr(self._reply_model, "close", None)
            try:
                try:
                    await self._memory_scheduler.close()
                finally:
                    if close_model is not None:
                        await close_model()
            finally:
                self._ledger.close()

    async def handle_turn(self, message: UserMessage) -> AsyncIterator[ReplyEvent]:
        request_id = message.request_id if isinstance(message.request_id, str) else ""
        validation_error = _validation_error(message, self._config.max_input_chars)
        if validation_error is not None:
            LOGGER.info("turn_rejected request_id=%s code=invalid_input", request_id)
            yield Failed(request_id, None, "invalid_input", False)
            return
        if self._closed:
            yield Failed(request_id, None, "ledger_unavailable", True)
            return
        if not await self._claim_request(request_id):
            yield Failed(request_id, None, "turn_in_progress", True)
            return

        try:
            conversation_slot = await self._acquire_conversation(
                message.conversation_id
            )
        except BaseException:
            await self._release_request(request_id)
            raise
        if conversation_slot is None:
            await self._release_request(request_id)
            yield Failed(request_id, None, "ledger_unavailable", True)
            return

        try:
            async with self._memory_scheduler.foreground():
                turn_stream = self._run_turn(message)
                try:
                    async for event in turn_stream:
                        yield event
                except (asyncio.CancelledError, GeneratorExit):
                    await turn_stream.aclose()
                    raise
        finally:
            await self._release_conversation(
                message.conversation_id, conversation_slot
            )
            await self._release_request(request_id)

    async def _run_turn(self, message: UserMessage) -> AsyncIterator[ReplyEvent]:
        turn_started = time.perf_counter()
        epoch_prepare_started = time.perf_counter()
        prompt_measure_ms = 0.0
        epoch_rolled_over = False
        try:
            user_result = self._ledger.find_user_message(message)
        except RequestConflictError:
            LOGGER.info(
                "turn_rejected request_id=%s code=request_conflict", message.request_id
            )
            yield Failed(message.request_id, None, "request_conflict", False)
            return
        except Exception as error:
            LOGGER.warning(
                "turn_failed request_id=%s code=ledger_unavailable error_type=%s",
                message.request_id,
                type(error).__name__,
            )
            yield Failed(message.request_id, None, "ledger_unavailable", True)
            return

        current_time = self._clock().astimezone(ZoneInfo(message.timezone))
        user_commit_ms = 0.0
        if user_result is None:
            try:
                preview = self._ledger.preview_user_message(
                    message, budget_version=self._config.context_budget_version
                )
                preview_context = self._context_assembler.assemble(
                    preview,
                    current_user_event_id=preview.message_events[-1].event_id,
                    current_user_text=message.text,
                    current_time=current_time,
                    current_timezone=message.timezone,
                    resolve_recall=False,
                )
            except Exception as error:
                LOGGER.warning(
                    "turn_failed request_id=%s code=ledger_unavailable error_type=%s",
                    message.request_id,
                    type(error).__name__,
                )
                yield Failed(message.request_id, None, "ledger_unavailable", True)
                return

            measure_started = time.perf_counter()
            try:
                base_tokens = await self._context_assembler.measure_base_prompt(
                    preview_context, self._reply_model.measure_prompt
                )
            except Exception as error:
                LOGGER.warning(
                    "turn_failed request_id=%s code=model_unavailable error_type=%s",
                    message.request_id,
                    type(error).__name__,
                )
                yield Failed(message.request_id, None, "model_unavailable", True)
                return
            prompt_measure_ms += _elapsed_ms(measure_started)
            rollover_from = (
                preview.epoch_id
                if base_tokens > preview_context.budget.maximum_prompt_tokens
                and bool(preview.history_events)
                else None
            )
            user_commit_started = time.perf_counter()
            try:
                user_result = self._ledger.append_user_message(
                    message,
                    rollover_from_epoch_id=rollover_from,
                    budget_version=self._config.context_budget_version,
                )
                user_commit_ms = _elapsed_ms(user_commit_started)
                epoch_rolled_over = rollover_from is not None
            except RequestConflictError:
                LOGGER.info(
                    "turn_rejected request_id=%s code=request_conflict",
                    message.request_id,
                )
                yield Failed(message.request_id, None, "request_conflict", False)
                return
            except Exception as error:
                LOGGER.warning(
                    "turn_failed request_id=%s code=ledger_unavailable error_type=%s",
                    message.request_id,
                    type(error).__name__,
                )
                yield Failed(message.request_id, None, "ledger_unavailable", True)
                return

        epoch_prepare_ms = _elapsed_ms(epoch_prepare_started)

        user_event = user_result.event
        try:
            completed = self._ledger.find_completed_reply(message.request_id)
        except Exception as error:
            LOGGER.warning(
                "turn_failed request_id=%s code=ledger_unavailable error_type=%s",
                message.request_id,
                type(error).__name__,
            )
            yield Failed(message.request_id, user_event.event_id, "ledger_unavailable", True)
            return

        if completed is not None:
            replay_started = time.perf_counter()
            yield TextDelta(message.request_id, completed.text)
            total_ms = _elapsed_ms(turn_started)
            yield Completed(
                request_id=message.request_id,
                user_event_id=user_event.event_id,
                assistant_event_id=completed.event_id,
                text=completed.text,
                metrics=TurnMetrics(
                    ledger_user_commit_ms=user_commit_ms,
                    model_first_delta_ms=None,
                    model_total_ms=0.0,
                    ledger_reply_commit_ms=0.0,
                    total_ms=total_ms,
                    output_chars=len(completed.text),
                    delta_count=1,
                    replayed=True,
                    epoch_prepare_ms=epoch_prepare_ms,
                ),
            )
            LOGGER.info(
                "turn_replayed request_id=%s user_event_id=%s assistant_event_id=%s replay_ms=%.3f",
                message.request_id,
                user_event.event_id,
                completed.event_id,
                _elapsed_ms(replay_started),
            )
            return

        try:
            epoch_snapshot = self._ledger.load_epoch_snapshot(user_event.event_id)
            context_started = time.perf_counter()
            reply_context = self._context_assembler.assemble(
                epoch_snapshot,
                current_user_event_id=user_event.event_id,
                current_user_text=message.text,
                current_time=current_time,
                current_timezone=message.timezone,
            )
            context_ms = _elapsed_ms(context_started)
        except Exception as error:
            LOGGER.warning(
                "turn_failed request_id=%s user_event_id=%s "
                "code=ledger_unavailable error_type=%s",
                message.request_id,
                user_event.event_id,
                type(error).__name__,
            )
            yield Failed(
                message.request_id, user_event.event_id, "ledger_unavailable", True
            )
            return

        measure_started = time.perf_counter()
        try:
            reply_context = await self._context_assembler.fit_to_budget(
                reply_context, self._reply_model.measure_prompt
            )
            prompt_measure_ms += _elapsed_ms(measure_started)
        except ContextBudgetExceeded as error:
            prompt_measure_ms += _elapsed_ms(measure_started)
            self._record_status_safely(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                user_event_id=user_event.event_id,
                event_type="turn_failed",
                code="context_budget_exceeded",
                partial_text="",
            )
            LOGGER.info(
                "turn_failed request_id=%s user_event_id=%s "
                "code=context_budget_exceeded prompt_tokens=%d maximum_tokens=%d",
                message.request_id,
                user_event.event_id,
                error.prompt_tokens,
                error.maximum_prompt_tokens,
            )
            yield Failed(
                message.request_id,
                user_event.event_id,
                "context_budget_exceeded",
                True,
            )
            return
        except Exception as error:
            prompt_measure_ms += _elapsed_ms(measure_started)
            self._record_status_safely(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                user_event_id=user_event.event_id,
                event_type="turn_failed",
                code="model_unavailable",
                partial_text="",
            )
            LOGGER.warning(
                "turn_failed request_id=%s user_event_id=%s "
                "code=model_unavailable error_type=%s",
                message.request_id,
                user_event.event_id,
                type(error).__name__,
            )
            yield Failed(
                message.request_id, user_event.event_id, "model_unavailable", True
            )
            return

        reply_request = ReplyRequest(
            request_id=message.request_id,
            conversation_id=message.conversation_id,
            user_event_id=user_event.event_id,
            text=message.text,
            context=reply_context,
        )
        chunks: list[str] = []
        delta_count = 0
        first_delta_ms: float | None = None
        model_started = time.perf_counter()

        try:
            async for chunk in self._reply_model.stream_reply(reply_request):
                if not isinstance(chunk, str):
                    raise TypeError("reply model yielded a non-string chunk")
                if not chunk:
                    continue
                chunks.append(chunk)
                delta_count += 1
                if first_delta_ms is None:
                    first_delta_ms = _elapsed_ms(model_started)
                yield TextDelta(message.request_id, chunk)
        except (asyncio.CancelledError, GeneratorExit):
            partial_text = "".join(chunks)
            self._record_status_safely(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                user_event_id=user_event.event_id,
                event_type="generation_cancelled",
                code="generation_cancelled",
                partial_text=partial_text,
            )
            LOGGER.info(
                "turn_cancelled request_id=%s user_event_id=%s emitted_chars=%d",
                message.request_id,
                user_event.event_id,
                len(partial_text),
            )
            raise
        except Exception as error:
            partial_text = "".join(chunks)
            self._record_status_safely(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                user_event_id=user_event.event_id,
                event_type="turn_failed",
                code="model_unavailable",
                partial_text=partial_text,
            )
            LOGGER.warning(
                "turn_failed request_id=%s user_event_id=%s code=model_unavailable "
                "error_type=%s emitted_chars=%d",
                message.request_id,
                user_event.event_id,
                type(error).__name__,
                len(partial_text),
            )
            yield Failed(
                message.request_id, user_event.event_id, "model_unavailable", True
            )
            return

        model_total_ms = _elapsed_ms(model_started)
        full_text = "".join(chunks)
        if not full_text.strip():
            self._record_status_safely(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                user_event_id=user_event.event_id,
                event_type="turn_failed",
                code="empty_model_response",
                partial_text=full_text,
            )
            LOGGER.info(
                "turn_failed request_id=%s user_event_id=%s code=empty_model_response",
                message.request_id,
                user_event.event_id,
            )
            yield Failed(
                message.request_id,
                user_event.event_id,
                "empty_model_response",
                True,
            )
            return

        reply_commit_started = time.perf_counter()
        try:
            assistant_event = self._ledger.append_character_message(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                user_event_id=user_event.event_id,
                text=full_text,
                enqueue_memory_background=self._memory_background_enabled,
            )
            reply_commit_ms = _elapsed_ms(reply_commit_started)
        except Exception as error:
            self._record_status_safely(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                user_event_id=user_event.event_id,
                event_type="turn_failed",
                code="commit_failed",
                partial_text=full_text,
            )
            LOGGER.warning(
                "turn_failed request_id=%s user_event_id=%s code=commit_failed "
                "error_type=%s emitted_chars=%d",
                message.request_id,
                user_event.event_id,
                type(error).__name__,
                len(full_text),
            )
            yield Failed(message.request_id, user_event.event_id, "commit_failed", True)
            return

        metrics = TurnMetrics(
            ledger_user_commit_ms=user_commit_ms,
            model_first_delta_ms=first_delta_ms,
            model_total_ms=model_total_ms,
            ledger_reply_commit_ms=reply_commit_ms,
            total_ms=_elapsed_ms(turn_started),
            output_chars=len(full_text),
            delta_count=delta_count,
            epoch_prepare_ms=epoch_prepare_ms,
            working_activation_ms=(
                0.0
                if reply_context.working_activation.explicit_recall_cues
                else context_ms
            ),
            recall_ms=(
                context_ms
                if reply_context.working_activation.explicit_recall_cues
                else 0.0
            ),
            prompt_measure_ms=prompt_measure_ms,
            history_event_count=len(reply_context.history_messages),
            recall_evidence_count=len(reply_context.recall_frame.evidence),
            prompt_tokens=reply_context.budget.total_prompt_tokens,
            epoch_rolled_over=epoch_rolled_over,
        )
        LOGGER.info(
            "turn_completed request_id=%s user_event_id=%s assistant_event_id=%s "
            "user_commit_ms=%.3f first_delta_ms=%s model_total_ms=%.3f "
            "reply_commit_ms=%.3f total_ms=%.3f output_chars=%d delta_count=%d "
            "epoch_prepare_ms=%.3f context_ms=%.3f prompt_measure_ms=%.3f "
            "history_events=%d recall_evidence=%d prompt_tokens=%d epoch_rolled_over=%s",
            message.request_id,
            user_event.event_id,
            assistant_event.event_id,
            metrics.ledger_user_commit_ms,
            (
                f"{metrics.model_first_delta_ms:.3f}"
                if metrics.model_first_delta_ms is not None
                else "none"
            ),
            metrics.model_total_ms,
            metrics.ledger_reply_commit_ms,
            metrics.total_ms,
            metrics.output_chars,
            metrics.delta_count,
            metrics.epoch_prepare_ms,
            metrics.working_activation_ms + metrics.recall_ms,
            metrics.prompt_measure_ms,
            metrics.history_event_count,
            metrics.recall_evidence_count,
            metrics.prompt_tokens,
            metrics.epoch_rolled_over,
        )
        if self._memory_background_enabled:
            await self._memory_scheduler.notify_new_job()
        yield Completed(
            request_id=message.request_id,
            user_event_id=user_event.event_id,
            assistant_event_id=assistant_event.event_id,
            text=full_text,
            metrics=metrics,
        )

    async def wait_for_memory_background_idle(
        self, timeout_seconds: float = 5.0
    ) -> None:
        await self._memory_scheduler.wait_idle(timeout_seconds)

    def memory_background_snapshot(self) -> BackgroundSchedulerSnapshot:
        return self._memory_scheduler.snapshot()

    def _record_status_safely(
        self,
        *,
        request_id: str,
        conversation_id: str,
        user_event_id: str,
        event_type: str,
        code: str,
        partial_text: str,
    ) -> None:
        try:
            self._ledger.append_turn_status(
                request_id=request_id,
                conversation_id=conversation_id,
                user_event_id=user_event_id,
                event_type=event_type,
                code=code,
                partial_text=partial_text,
            )
        except Exception as error:
            LOGGER.error(
                "turn_status_commit_failed request_id=%s user_event_id=%s code=%s "
                "error_type=%s",
                request_id,
                user_event_id,
                code,
                type(error).__name__,
            )

    async def _claim_request(self, request_id: str) -> bool:
        async with self._active_guard:
            if request_id in self._active_requests:
                return False
            self._active_requests.add(request_id)
            return True

    async def _release_request(self, request_id: str) -> None:
        async with self._active_guard:
            self._active_requests.discard(request_id)

    async def _acquire_conversation(
        self, conversation_id: str
    ) -> _ConversationSlot | None:
        async with self._conversation_slots_guard:
            if self._closed:
                return None
            slot = self._conversation_slots.get(conversation_id)
            if slot is None:
                slot = _ConversationSlot(asyncio.Lock())
                self._conversation_slots[conversation_id] = slot
            slot.users += 1
        try:
            await slot.lock.acquire()
        except BaseException:
            async with self._conversation_slots_guard:
                slot.users -= 1
                if slot.users == 0:
                    self._conversation_slots.pop(conversation_id, None)
            raise
        if self._closed:
            await self._release_conversation(conversation_id, slot)
            return None
        return slot

    async def _release_conversation(
        self, conversation_id: str, slot: _ConversationSlot
    ) -> None:
        slot.lock.release()
        async with self._conversation_slots_guard:
            slot.users -= 1
            if slot.users == 0:
                self._conversation_slots.pop(conversation_id, None)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
