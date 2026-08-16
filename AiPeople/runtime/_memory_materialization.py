from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ._ledger import EventRecord
from ._memory_contracts import (
    EvidenceSource,
    MemoryContractError,
    MemoryEvidence,
    MemoryRepresentation,
    MemoryTemporal,
    validate_memory_representation,
)
from ._memory_propose import (
    MemoryProposeError,
    MemoryProposeRequest,
    MemoryProposeResult,
    validate_memory_propose_result,
)


MEMORY_MATERIALIZATION_FAILURE_CODES = frozenset(
    {
        "memory_materialize_contract_invalid",
        "memory_materialize_event_missing",
        "memory_materialize_event_mismatch",
        "memory_materialize_quote_missing",
        "memory_materialize_quote_ambiguous",
        "memory_materialize_temporal_invalid",
        "memory_materialize_representation_invalid",
    }
)


class EventReader(Protocol):
    def get_event(self, event_id: str) -> EventRecord | None: ...


Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class MemoryMaterializationError(ValueError):
    def __init__(self, message: str, *, code: str, path: str | None = None) -> None:
        super().__init__(message)
        if code not in MEMORY_MATERIALIZATION_FAILURE_CODES:
            raise ValueError("unknown memory materialization failure code")
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class MaterializationBatch:
    proposal_run_id: str
    representations: tuple[MemoryRepresentation, ...]


def materialize_memory_proposals(
    request: MemoryProposeRequest,
    result: MemoryProposeResult,
    event_reader: EventReader,
    *,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
) -> MaterializationBatch:
    if type(request) is not MemoryProposeRequest:
        raise TypeError("request must be MemoryProposeRequest")
    if type(result) is not MemoryProposeResult:
        raise TypeError("result must be MemoryProposeResult")
    if not callable(getattr(event_reader, "get_event", None)):
        raise TypeError("event_reader must provide get_event(event_id)")
    try:
        validate_memory_propose_result(result, request)
    except (MemoryProposeError, MemoryContractError) as error:
        raise MemoryMaterializationError(
            str(error),
            code="memory_materialize_contract_invalid",
            path=getattr(error, "path", None),
        ) from error
    if not result.proposals:
        return MaterializationBatch(result.proposal_run_id, ())

    event_sources = _verify_event_window(request, event_reader)
    created_at = _utc_z((clock or _utc_now)(), "created_at")
    make_id = id_factory or (lambda: str(uuid.uuid4()))
    representations = []
    for ordinal, proposal in enumerate(result.proposals):
        evidence = tuple(
            _materialize_quote(quote, event_sources, ordinal, quote_index)
            for quote_index, quote in enumerate(proposal.evidence_quotes)
        )
        temporal = _normalize_temporal(proposal.temporal, ordinal)
        try:
            representation = MemoryRepresentation(
                schema_version=1,
                memory_id=f"memory-{make_id()}",
                version=1,
                conversation_id=request.conversation_id,
                proposal_run_id=request.proposal_run_id,
                proposal_ordinal=ordinal,
                kind=proposal.kind,
                statement=proposal.statement,
                subject=proposal.subject,
                epistemic=proposal.epistemic,
                temporal=temporal,
                evidence=evidence,
                semantic_reason=proposal.semantic_reason,
                confidence=proposal.confidence,
                relations=proposal.relation_suggestions,
                status="proposed",
                created_at=created_at,
                idempotency_key=f"{request.proposal_run_id}:{ordinal}",
            )
            validate_memory_representation(
                representation, evidence_sources=event_sources
            )
        except MemoryContractError as error:
            raise MemoryMaterializationError(
                str(error),
                code="memory_materialize_representation_invalid",
                path=getattr(error, "path", None),
            ) from error
        representations.append(representation)
    return MaterializationBatch(result.proposal_run_id, tuple(representations))


def _verify_event_window(
    request: MemoryProposeRequest, event_reader: EventReader
) -> dict[str, EvidenceSource]:
    sources: dict[str, EvidenceSource] = {}
    for index, snapshot in enumerate(request.events):
        stored = event_reader.get_event(snapshot.event_id)
        path = f"events[{index}]"
        if stored is None:
            raise MemoryMaterializationError(
                "MEMORY_PROPOSE source event does not exist in the ledger",
                code="memory_materialize_event_missing",
                path=f"{path}.event_id",
            )
        expected = (
            snapshot.conversation_id,
            snapshot.sequence_no,
            snapshot.actor,
            snapshot.event_type,
            snapshot.occurred_at,
            snapshot.timezone,
            snapshot.text,
        )
        actual = (
            stored.conversation_id,
            stored.sequence_no,
            stored.actor,
            stored.event_type,
            stored.occurred_at,
            stored.occurred_timezone,
            stored.text,
        )
        if actual != expected or stored.payload.get("status") != "complete":
            raise MemoryMaterializationError(
                "MEMORY_PROPOSE event snapshot does not match committed ledger evidence",
                code="memory_materialize_event_mismatch",
                path=path,
            )
        sources[stored.event_id] = EvidenceSource(
            event_id=stored.event_id,
            conversation_id=stored.conversation_id,
            actor=stored.actor,
            event_type=stored.event_type,
            committed=True,
            text=stored.text,
        )
    return sources


def _materialize_quote(quote, event_sources, proposal_index, quote_index):
    path = f"proposals[{proposal_index}].evidence_quotes[{quote_index}]"
    source = event_sources.get(quote.event_id)
    if source is None:
        raise MemoryMaterializationError(
            "evidence event is absent from the verified ledger window",
            code="memory_materialize_event_missing",
            path=f"{path}.event_id",
        )
    if quote.start_hint is not None:
        start = quote.start_hint
        if source.text[start : start + len(quote.quote)] != quote.quote:
            raise MemoryMaterializationError(
                "evidence quote no longer matches its start hint",
                code="memory_materialize_quote_missing",
                path=f"{path}.start_hint",
            )
    else:
        starts = _all_occurrences(source.text, quote.quote)
        if not starts:
            raise MemoryMaterializationError(
                "evidence quote is absent from the committed event",
                code="memory_materialize_quote_missing",
                path=f"{path}.quote",
            )
        if len(starts) != 1:
            raise MemoryMaterializationError(
                "repeated evidence quote requires an exact start_hint",
                code="memory_materialize_quote_ambiguous",
                path=f"{path}.start_hint",
            )
        start = starts[0]
    return MemoryEvidence(
        event_id=quote.event_id,
        role=quote.role,
        excerpt_start=start,
        excerpt_end=start + len(quote.quote),
        excerpt=quote.quote,
        excerpt_sha256=hashlib.sha256(quote.quote.encode("utf-8")).hexdigest(),
    )


def _normalize_temporal(value: MemoryTemporal, proposal_index: int) -> MemoryTemporal:
    try:
        start_at = _optional_utc_z(value.start_at, "temporal.start_at")
        end_at = _optional_utc_z(value.end_at, "temporal.end_at")
    except (TypeError, ValueError) as error:
        raise MemoryMaterializationError(
            str(error),
            code="memory_materialize_temporal_invalid",
            path=f"proposals[{proposal_index}].temporal",
        ) from error
    return MemoryTemporal(
        relation=value.relation,
        resolution=value.resolution,
        source_text=value.source_text,
        anchor_event_id=value.anchor_event_id,
        start_at=start_at,
        end_at=end_at,
        timezone=value.timezone,
        precision=value.precision,
    )


def _all_occurrences(text: str, quote: str) -> tuple[int, ...]:
    starts = []
    offset = 0
    while True:
        found = text.find(quote, offset)
        if found < 0:
            return tuple(starts)
        starts.append(found)
        offset = found + 1


def _optional_utc_z(value: str | None, name: str) -> str | None:
    return None if value is None else _utc_z(datetime.fromisoformat(value.replace("Z", "+00:00")), name)


def _utc_z(value: datetime, name: str) -> str:
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    normalized = value.astimezone(timezone.utc)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
