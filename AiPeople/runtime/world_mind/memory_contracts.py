from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence, runtime_checkable

from runtime._memory_contracts import (
    EvidenceQuote,
    MemoryEpistemic,
    MemoryEvidence,
    MemoryProposalDraft,
    MemoryRelations,
    MemorySubject,
    MemoryTemporal,
    memory_contract_to_dict,
    memory_proposal_draft_from_dict,
)


MEMORY_V2_SCHEMA_VERSION = 2
MEMORY_V2_STATUSES = frozenset({"active", "disputed", "superseded", "rejected"})


class HeroineMemoryContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HeroineMemorySourceEvent:
    event_id: str
    request_id: str
    conversation_id: str
    sequence_no: int
    actor: str
    event_type: str
    text: str
    game_time: datetime

    def __post_init__(self) -> None:
        _identifier(self.event_id, "event_id")
        _identifier(self.request_id, "request_id")
        _identifier(self.conversation_id, "conversation_id")
        if type(self.sequence_no) is not int or self.sequence_no <= 0:
            raise HeroineMemoryContractError("sequence_no must be a positive integer")
        if self.actor not in {"protagonist", "heroine"}:
            raise HeroineMemoryContractError("actor must be protagonist or heroine")
        if self.event_type not in {"utterance", "reply"}:
            raise HeroineMemoryContractError("event_type must be utterance or reply")
        _text(self.text, "text")
        _game_time(self.game_time)


@dataclass(frozen=True, slots=True)
class HeroineMemoryProposeRequest:
    save_id: str
    world_id: str
    owner_character_id: str
    source_events: tuple[HeroineMemorySourceEvent, ...]
    existing_memories: tuple["HeroineMemoryRepresentation", ...]

    def __post_init__(self) -> None:
        _identifier(self.save_id, "save_id")
        _identifier(self.world_id, "world_id")
        _identifier(self.owner_character_id, "owner_character_id")
        if not self.source_events:
            raise HeroineMemoryContractError("source_events must not be empty")
        _typed_tuple(self.source_events, HeroineMemorySourceEvent, "source_events")
        _typed_tuple(
            self.existing_memories,
            HeroineMemoryRepresentation,
            "existing_memories",
        )
        if len({event.event_id for event in self.source_events}) != len(self.source_events):
            raise HeroineMemoryContractError("source_events must be unique")
        for memory in self.existing_memories:
            if (
                memory.save_id != self.save_id
                or memory.world_id != self.world_id
                or memory.owner_character_id != self.owner_character_id
            ):
                raise HeroineMemoryContractError("existing memory escaped repository ownership")


@runtime_checkable
class HeroineMemoryProposer(Protocol):
    async def propose(
        self, request: HeroineMemoryProposeRequest
    ) -> Sequence[MemoryProposalDraft]: ...


@dataclass(frozen=True, slots=True)
class HeroineMemoryProposal:
    proposal_id: str
    save_id: str
    world_id: str
    owner_character_id: str
    conversation_id: str | None
    source_event_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    observer_ids: tuple[str, ...]
    game_time: datetime
    draft: MemoryProposalDraft

    def __post_init__(self) -> None:
        _identifier(self.proposal_id, "proposal_id")
        _identity_fields(self)
        if self.owner_character_id not in set(self.participant_ids) | set(self.observer_ids):
            raise HeroineMemoryContractError(
                "memory owner must be a participant or observer"
            )
        if not isinstance(self.draft, MemoryProposalDraft):
            raise TypeError("draft must be MemoryProposalDraft")
        evidence_ids = {quote.event_id for quote in self.draft.evidence_quotes}
        if not evidence_ids <= set(self.source_event_ids):
            raise HeroineMemoryContractError(
                "every evidence quote must belong to source_event_ids"
            )


@dataclass(frozen=True, slots=True)
class HeroineMemoryRepresentation:
    schema_version: int
    memory_id: str
    version: int
    save_id: str
    world_id: str
    owner_character_id: str
    conversation_id: str | None
    source_event_ids: tuple[str, ...]
    participant_ids: tuple[str, ...]
    observer_ids: tuple[str, ...]
    game_time: datetime
    kind: str
    statement: str
    subject: MemorySubject
    epistemic: MemoryEpistemic
    temporal: MemoryTemporal
    evidence: tuple[MemoryEvidence, ...]
    semantic_reason: str
    confidence: int | float
    relations: MemoryRelations
    status: str
    created_at: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_V2_SCHEMA_VERSION:
            raise HeroineMemoryContractError("schema_version must equal 2")
        _identifier(self.memory_id, "memory_id")
        if type(self.version) is not int or self.version <= 0:
            raise HeroineMemoryContractError("version must be a positive integer")
        _identity_fields(self)
        if self.owner_character_id not in set(self.participant_ids) | set(self.observer_ids):
            raise HeroineMemoryContractError(
                "memory owner must be a participant or observer"
            )
        if self.status not in MEMORY_V2_STATUSES:
            raise HeroineMemoryContractError("unsupported memory status")
        if not self.evidence:
            raise HeroineMemoryContractError("evidence must not be empty")
        _typed_tuple(self.evidence, MemoryEvidence, "evidence")
        evidence_ids = {item.event_id for item in self.evidence}
        if not evidence_ids <= set(self.source_event_ids):
            raise HeroineMemoryContractError(
                "every evidence item must belong to source_event_ids"
            )
        MemoryProposalDraft(
            kind=self.kind,
            statement=self.statement,
            subject=self.subject,
            epistemic=self.epistemic,
            temporal=self.temporal,
            evidence_quotes=tuple(
                EvidenceQuote(
                    event_id=item.event_id,
                    role=item.role,
                    quote=item.excerpt,
                    start_hint=item.excerpt_start,
                )
                for item in self.evidence
            ),
            semantic_reason=self.semantic_reason,
            confidence=self.confidence,
            relation_suggestions=self.relations,
        )
        _identifier(self.idempotency_key, "idempotency_key")
        _text(self.created_at, "created_at")


def heroine_memory_to_dict(memory: HeroineMemoryRepresentation) -> dict[str, Any]:
    if not isinstance(memory, HeroineMemoryRepresentation):
        raise TypeError("memory must be HeroineMemoryRepresentation")
    draft = MemoryProposalDraft(
        kind=memory.kind,
        statement=memory.statement,
        subject=memory.subject,
        epistemic=memory.epistemic,
        temporal=memory.temporal,
        evidence_quotes=tuple(
            EvidenceQuote(
                event_id=item.event_id,
                role=item.role,
                quote=item.excerpt,
                start_hint=item.excerpt_start,
            )
            for item in memory.evidence
        ),
        semantic_reason=memory.semantic_reason,
        confidence=memory.confidence,
        relation_suggestions=memory.relations,
    )
    semantic = memory_contract_to_dict(draft)
    semantic.pop("evidence_quotes")
    relations = semantic.pop("relation_suggestions")
    return {
        "schema_version": memory.schema_version,
        "memory_id": memory.memory_id,
        "version": memory.version,
        "save_id": memory.save_id,
        "world_id": memory.world_id,
        "owner_character_id": memory.owner_character_id,
        "conversation_id": memory.conversation_id,
        "source_event_ids": list(memory.source_event_ids),
        "participant_ids": list(memory.participant_ids),
        "observer_ids": list(memory.observer_ids),
        "game_time": memory.game_time.isoformat(timespec="microseconds"),
        **semantic,
        "evidence": [
            {
                "event_id": item.event_id,
                "role": item.role,
                "excerpt_start": item.excerpt_start,
                "excerpt_end": item.excerpt_end,
                "excerpt": item.excerpt,
                "excerpt_sha256": item.excerpt_sha256,
            }
            for item in memory.evidence
        ],
        "relations": relations,
        "status": memory.status,
        "created_at": memory.created_at,
        "idempotency_key": memory.idempotency_key,
    }


def canonical_heroine_memory_json(memory: HeroineMemoryRepresentation) -> bytes:
    return json.dumps(
        heroine_memory_to_dict(memory),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def heroine_memory_from_json(value: str | bytes) -> HeroineMemoryRepresentation:
    raw = json.loads(value)
    if not isinstance(raw, dict):
        raise HeroineMemoryContractError("memory JSON must be an object")
    semantic = {
        key: raw[key]
        for key in (
            "kind",
            "statement",
            "subject",
            "epistemic",
            "temporal",
            "semantic_reason",
            "confidence",
        )
    }
    semantic["evidence_quotes"] = [
        {
            "event_id": item["event_id"],
            "role": item["role"],
            "quote": item["excerpt"],
            "start_hint": item["excerpt_start"],
        }
        for item in raw["evidence"]
    ]
    semantic["relation_suggestions"] = raw["relations"]
    draft = memory_proposal_draft_from_dict(semantic)
    game_time = datetime.fromisoformat(raw["game_time"])
    evidence = tuple(
        MemoryEvidence(
            event_id=item["event_id"],
            role=item["role"],
            excerpt_start=item["excerpt_start"],
            excerpt_end=item["excerpt_end"],
            excerpt=item["excerpt"],
            excerpt_sha256=item["excerpt_sha256"],
        )
        for item in raw["evidence"]
    )
    return HeroineMemoryRepresentation(
        schema_version=raw["schema_version"],
        memory_id=raw["memory_id"],
        version=raw["version"],
        save_id=raw["save_id"],
        world_id=raw["world_id"],
        owner_character_id=raw["owner_character_id"],
        conversation_id=raw.get("conversation_id"),
        source_event_ids=tuple(raw["source_event_ids"]),
        participant_ids=tuple(raw["participant_ids"]),
        observer_ids=tuple(raw["observer_ids"]),
        game_time=game_time,
        kind=draft.kind,
        statement=draft.statement,
        subject=draft.subject,
        epistemic=draft.epistemic,
        temporal=draft.temporal,
        evidence=evidence,
        semantic_reason=draft.semantic_reason,
        confidence=draft.confidence,
        relations=draft.relation_suggestions,
        status=raw["status"],
        created_at=raw["created_at"],
        idempotency_key=raw["idempotency_key"],
    )


def _identity_fields(value: object) -> None:
    _identifier(getattr(value, "save_id"), "save_id")
    _identifier(getattr(value, "world_id"), "world_id")
    _identifier(getattr(value, "owner_character_id"), "owner_character_id")
    conversation_id = getattr(value, "conversation_id")
    if conversation_id is not None:
        _identifier(conversation_id, "conversation_id")
    _identifier_tuple(getattr(value, "source_event_ids"), "source_event_ids", required=True)
    _identifier_tuple(getattr(value, "participant_ids"), "participant_ids", required=True)
    _identifier_tuple(getattr(value, "observer_ids"), "observer_ids", required=False)
    _game_time(getattr(value, "game_time"))


def _identifier_tuple(values: object, name: str, *, required: bool) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple")
    if required and not values:
        raise HeroineMemoryContractError(f"{name} must not be empty")
    for value in values:
        _identifier(value, name)
    if len(set(values)) != len(values):
        raise HeroineMemoryContractError(f"{name} must be unique")


def _typed_tuple(values: object, item_type: type, name: str) -> None:
    if not isinstance(values, tuple) or any(not isinstance(item, item_type) for item in values):
        raise TypeError(f"{name} must be a tuple of {item_type.__name__}")


def _identifier(value: object, name: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise HeroineMemoryContractError(f"{name} must be a non-empty identifier")


def _text(value: object, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise HeroineMemoryContractError(f"{name} must be non-empty text")


def _game_time(value: object) -> None:
    if type(value) is not datetime or value.tzinfo is not None:
        raise HeroineMemoryContractError("game_time must be a naive game datetime")
