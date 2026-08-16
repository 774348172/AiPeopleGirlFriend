from __future__ import annotations

import asyncio
from typing import Protocol

from ._ledger import EventLedger
from ._memory_contracts import MEMORY_INDEXABLE_STATUSES
from ._memory_materialization import (
    MemoryMaterializationError,
    materialize_memory_proposals,
)
from ._memory_propose import (
    MEMORY_PROPOSE_LIMITS,
    MEMORY_PROPOSE_MODE,
    MEMORY_PROPOSE_RETRYABLE_FAILURES,
    ExistingMemoryContext,
    MemoryProposeError,
    MemoryProposeEvent,
    MemoryProposeRequest,
    build_memory_propose_messages,
    parse_memory_propose_output,
)
from ._memory_scheduler import BackgroundJobFailure, MemoryBackgroundJob
from ._memory_store import MemoryStoreError


class MemoryProposeModel(Protocol):
    async def generate_memory_propose(
        self,
        request: MemoryProposeRequest,
        messages: tuple[dict[str, str], dict[str, str]],
    ) -> str | bytes: ...


class MemoryProposalPipeline:
    def __init__(self, ledger: EventLedger, model: MemoryProposeModel) -> None:
        if not callable(getattr(model, "generate_memory_propose", None)):
            raise TypeError("memory propose model must provide generate_memory_propose()")
        self._ledger = ledger
        self._model = model

    async def run_memory_propose(self, job: MemoryBackgroundJob) -> None:
        store = self._ledger.memory_store()
        run = store.begin_run(
            proposal_run_id=job.proposal_run_id,
            conversation_id=job.conversation_id,
            from_sequence_no=job.from_sequence_no,
            through_sequence_no=job.through_sequence_no,
            idempotency_key=f"{job.job_id}:begin:{job.attempt}",
        )
        if run.state == "committed":
            return
        request = self._build_request(job, store)
        try:
            raw = await self._model.generate_memory_propose(
                request, build_memory_propose_messages(request)
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            self._record_failure(store, job, "memory_propose_timeout", retryable=True)
            raise BackgroundJobFailure(
                "memory_propose_timeout", retryable=True
            ) from error
        except Exception as error:
            self._record_failure(
                store, job, "memory_propose_model_unavailable", retryable=True
            )
            raise BackgroundJobFailure(
                "memory_propose_model_unavailable", retryable=True
            ) from error

        try:
            result = parse_memory_propose_output(raw, request)
            batch = materialize_memory_proposals(
                request,
                result,
                self._ledger,
                clock=self._ledger._clock,
                id_factory=self._ledger._id_factory,
            )
        except MemoryProposeError as error:
            retryable = error.code in MEMORY_PROPOSE_RETRYABLE_FAILURES
            self._record_failure(store, job, error.code, retryable=retryable)
            raise BackgroundJobFailure(error.code, retryable=retryable) from error
        except MemoryMaterializationError as error:
            self._record_failure(store, job, error.code, retryable=False)
            raise BackgroundJobFailure(error.code, retryable=False) from error

        try:
            store.commit_batch(
                batch, idempotency_key=f"{job.job_id}:commit:{job.attempt}"
            )
        except MemoryStoreError as error:
            self._record_failure(
                store, job, "memory_commit_unavailable", retryable=True
            )
            raise BackgroundJobFailure(
                "memory_commit_unavailable", retryable=True
            ) from error

    def _build_request(
        self, job: MemoryBackgroundJob, store
    ) -> MemoryProposeRequest:
        events = tuple(
            event
            for event in self._ledger.list_events(job.conversation_id)
            if job.from_sequence_no <= event.sequence_no <= job.through_sequence_no
        )
        snapshots = tuple(
            MemoryProposeEvent(
                event_id=event.event_id,
                conversation_id=event.conversation_id,
                sequence_no=event.sequence_no,
                actor=event.actor,
                event_type=event.event_type,
                occurred_at=event.occurred_at,
                timezone=event.occurred_timezone,
                text=event.text,
            )
            for event in events
        )
        existing = tuple(
            ExistingMemoryContext(
                memory_id=memory.memory_id,
                conversation_id=memory.conversation_id,
                kind=memory.kind,
                statement=memory.statement,
                subject=memory.subject,
                status=memory.status,
            )
            for memory in store.list_memories(conversation_id=job.conversation_id)
            if memory.status in MEMORY_INDEXABLE_STATUSES
        )[: MEMORY_PROPOSE_LIMITS["existing_memories_max"]]
        return MemoryProposeRequest(
            schema_version=1,
            mode=MEMORY_PROPOSE_MODE,
            proposal_run_id=job.proposal_run_id,
            conversation_id=job.conversation_id,
            from_sequence_no=job.from_sequence_no,
            through_sequence_no=job.through_sequence_no,
            events=snapshots,
            existing_memories=existing,
            max_proposals=MEMORY_PROPOSE_LIMITS["proposals_max"],
        )

    @staticmethod
    def _record_failure(
        store,
        job: MemoryBackgroundJob,
        code: str,
        *,
        retryable: bool,
    ) -> None:
        store.record_failure(
            proposal_run_id=job.proposal_run_id,
            failure_code=code,
            retryable=retryable,
            idempotency_key=f"{job.job_id}:failure:{job.attempt}",
        )
