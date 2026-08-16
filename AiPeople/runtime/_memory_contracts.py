from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MEMORY_SCHEMA_VERSION: Final = 1

MEMORY_KINDS: Final = (
    "player_fact",
    "preference_boundary",
    "person_relation",
    "shared_experience",
    "relationship_meaning",
    "future_event",
    "unfinished_topic",
    "character_self_claim",
)
SUBJECT_TYPES: Final = (
    "player",
    "character",
    "both",
    "relationship",
    "third_party",
)
EPISTEMIC_POLARITIES: Final = ("affirmed", "negated")
EPISTEMIC_MODALITIES: Final = (
    "asserted",
    "uncertain",
    "hypothetical",
    "joking",
    "quoted",
)
EPISTEMIC_GROUNDINGS: Final = (
    "speaker_report",
    "acknowledged",
    "mutually_confirmed",
    "runtime_observed",
)
TEMPORAL_RELATIONS: Final = (
    "past",
    "present",
    "future",
    "atemporal",
    "unknown",
)
TEMPORAL_RESOLUTIONS: Final = (
    "resolved",
    "relative",
    "ambiguous",
    "not_applicable",
)
TEMPORAL_PRECISIONS: Final = (
    "instant",
    "minute",
    "hour",
    "part_of_day",
    "day",
    "range",
    "unknown",
    "not_applicable",
)
EVIDENCE_ROLES: Final = ("support", "contradict", "correction", "context")
MEMORY_RELATION_TYPES: Final = ("supersedes", "contradicts", "refines")
MEMORY_STATUSES: Final = (
    "proposed",
    "active",
    "disputed",
    "superseded",
    "rejected",
)
MEMORY_INDEXABLE_STATUSES: Final = ("active", "disputed")
MEMORY_TERMINAL_STATUSES: Final = ("superseded", "rejected")

DRAFT_FIELDS: Final = (
    "kind",
    "statement",
    "subject",
    "epistemic",
    "temporal",
    "evidence_quotes",
    "semantic_reason",
    "confidence",
    "relation_suggestions",
)
REPRESENTATION_FIELDS: Final = (
    "schema_version",
    "memory_id",
    "version",
    "conversation_id",
    "proposal_run_id",
    "proposal_ordinal",
    "kind",
    "statement",
    "subject",
    "epistemic",
    "temporal",
    "evidence",
    "semantic_reason",
    "confidence",
    "relations",
    "status",
    "created_at",
    "idempotency_key",
)

SUBJECT_FIELDS: Final = ("type", "entity_id", "display_name")
EPISTEMIC_FIELDS: Final = ("polarity", "modality", "grounding")
TEMPORAL_FIELDS: Final = (
    "relation",
    "resolution",
    "source_text",
    "anchor_event_id",
    "start_at",
    "end_at",
    "timezone",
    "precision",
)
EVIDENCE_QUOTE_FIELDS: Final = ("event_id", "role", "quote", "start_hint")
EVIDENCE_FIELDS: Final = (
    "event_id",
    "role",
    "excerpt_start",
    "excerpt_end",
    "excerpt",
    "excerpt_sha256",
)
RELATION_FIELDS: Final = (
    "semantic_slot",
    "supersedes",
    "contradicts",
    "refines",
)

FIELD_OWNERS: Final = MappingProxyType(
    {
        "schema_version": "runtime",
        "memory_id": "runtime",
        "version": "runtime",
        "conversation_id": "runtime",
        "proposal_run_id": "runtime",
        "proposal_ordinal": "runtime",
        "kind": "ai",
        "statement": "ai",
        "subject": "ai",
        "epistemic": "ai",
        "temporal": "ai_runtime",
        "evidence": "runtime",
        "semantic_reason": "ai",
        "confidence": "ai",
        "relations": "ai_runtime",
        "status": "runtime",
        "created_at": "runtime",
        "idempotency_key": "runtime",
    }
)

FIELD_LIMITS: Final = MappingProxyType(
    {
        "identifier_chars_max": 256,
        "statement_chars_min": 1,
        "statement_chars_max": 500,
        "semantic_reason_chars_min": 1,
        "semantic_reason_chars_max": 300,
        "evidence_count_min": 1,
        "evidence_count_max": 8,
        "evidence_quote_chars_min": 1,
        "evidence_quote_chars_max": 500,
        "display_name_chars_max": 100,
        "source_text_chars_max": 200,
        "semantic_slot_chars_max": 200,
        "timezone_chars_max": 128,
        "relation_targets_per_type_max": 16,
        "confidence_min": 0.0,
        "confidence_max": 1.0,
        "version_min": 1,
        "proposal_ordinal_min": 0,
    }
)

INITIAL_MEMORY_STATUS: Final = "proposed"
MEMORY_STATUS_TRANSITIONS: Final = MappingProxyType(
    {
        "proposed": frozenset({"active", "rejected", "disputed"}),
        "active": frozenset({"disputed", "superseded"}),
        "disputed": frozenset({"active", "superseded", "rejected"}),
        "superseded": frozenset(),
        "rejected": frozenset(),
    }
)


_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class MemoryContractError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "memory_schema_invalid",
        path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    event_id: str
    conversation_id: str
    actor: str
    event_type: str
    committed: bool
    text: str

    def __post_init__(self) -> None:
        _require_identifier(self.event_id, "evidence_source.event_id")
        _require_identifier(
            self.conversation_id, "evidence_source.conversation_id"
        )
        _require_enum(
            self.actor, ("user", "character", "system"), "evidence_source.actor"
        )
        _require_text(self.event_type, "evidence_source.event_type", maximum=64)
        if type(self.committed) is not bool:
            raise TypeError("evidence_source.committed must be a bool")
        if type(self.text) is not str:
            raise TypeError("evidence_source.text must be a string")


@dataclass(frozen=True, slots=True)
class MemorySubject:
    subject_type: str
    entity_id: str | None
    display_name: str | None

    def __post_init__(self) -> None:
        _require_enum(self.subject_type, SUBJECT_TYPES, "subject.type")
        _require_optional_identifier(self.entity_id, "subject.entity_id")
        _require_optional_text(
            self.display_name,
            "subject.display_name",
            maximum=FIELD_LIMITS["display_name_chars_max"],
        )


@dataclass(frozen=True, slots=True)
class MemoryEpistemic:
    polarity: str
    modality: str
    grounding: str

    def __post_init__(self) -> None:
        _require_enum(self.polarity, EPISTEMIC_POLARITIES, "epistemic.polarity")
        _require_enum(self.modality, EPISTEMIC_MODALITIES, "epistemic.modality")
        _require_enum(self.grounding, EPISTEMIC_GROUNDINGS, "epistemic.grounding")


@dataclass(frozen=True, slots=True)
class MemoryTemporal:
    relation: str
    resolution: str
    source_text: str | None
    anchor_event_id: str | None
    start_at: str | None
    end_at: str | None
    timezone: str | None
    precision: str

    def __post_init__(self) -> None:
        _require_enum(self.relation, TEMPORAL_RELATIONS, "temporal.relation")
        _require_enum(self.resolution, TEMPORAL_RESOLUTIONS, "temporal.resolution")
        _require_optional_text(
            self.source_text,
            "temporal.source_text",
            maximum=FIELD_LIMITS["source_text_chars_max"],
        )
        _require_optional_identifier(
            self.anchor_event_id, "temporal.anchor_event_id"
        )
        _require_optional_rfc3339(self.start_at, "temporal.start_at")
        _require_optional_rfc3339(self.end_at, "temporal.end_at")
        _require_optional_text(
            self.timezone,
            "temporal.timezone",
            maximum=FIELD_LIMITS["timezone_chars_max"],
        )
        _require_enum(self.precision, TEMPORAL_PRECISIONS, "temporal.precision")


@dataclass(frozen=True, slots=True)
class EvidenceQuote:
    event_id: str
    role: str
    quote: str
    start_hint: int | None

    def __post_init__(self) -> None:
        _require_identifier(self.event_id, "evidence_quote.event_id")
        _require_enum(self.role, EVIDENCE_ROLES, "evidence_quote.role")
        _require_text(
            self.quote,
            "evidence_quote.quote",
            minimum=FIELD_LIMITS["evidence_quote_chars_min"],
            maximum=FIELD_LIMITS["evidence_quote_chars_max"],
        )
        _require_optional_integer(
            self.start_hint, "evidence_quote.start_hint", minimum=0
        )


@dataclass(frozen=True, slots=True)
class MemoryEvidence:
    event_id: str
    role: str
    excerpt_start: int
    excerpt_end: int
    excerpt: str
    excerpt_sha256: str

    def __post_init__(self) -> None:
        _require_identifier(self.event_id, "evidence.event_id")
        _require_enum(self.role, EVIDENCE_ROLES, "evidence.role")
        _require_integer(self.excerpt_start, "evidence.excerpt_start", minimum=0)
        _require_integer(self.excerpt_end, "evidence.excerpt_end", minimum=1)
        _require_text(
            self.excerpt,
            "evidence.excerpt",
            minimum=FIELD_LIMITS["evidence_quote_chars_min"],
            maximum=FIELD_LIMITS["evidence_quote_chars_max"],
        )
        _require_text(self.excerpt_sha256, "evidence.excerpt_sha256")
        if not _SHA256_PATTERN.fullmatch(self.excerpt_sha256):
            raise MemoryContractError("evidence.excerpt_sha256 must be lowercase SHA256")


@dataclass(frozen=True, slots=True)
class MemoryRelations:
    semantic_slot: str | None
    supersedes: tuple[str, ...]
    contradicts: tuple[str, ...]
    refines: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_optional_text(
            self.semantic_slot,
            "relations.semantic_slot",
            maximum=FIELD_LIMITS["semantic_slot_chars_max"],
        )
        for name, values in (
            ("supersedes", self.supersedes),
            ("contradicts", self.contradicts),
            ("refines", self.refines),
        ):
            _require_identifier_tuple(
                values,
                f"relations.{name}",
                maximum=FIELD_LIMITS["relation_targets_per_type_max"],
            )


@dataclass(frozen=True, slots=True)
class MemoryProposalDraft:
    kind: str
    statement: str
    subject: MemorySubject
    epistemic: MemoryEpistemic
    temporal: MemoryTemporal
    evidence_quotes: tuple[EvidenceQuote, ...]
    semantic_reason: str
    confidence: int | float
    relation_suggestions: MemoryRelations

    def __post_init__(self) -> None:
        _require_enum(self.kind, MEMORY_KINDS, "kind")
        _require_text(
            self.statement,
            "statement",
            minimum=FIELD_LIMITS["statement_chars_min"],
            maximum=FIELD_LIMITS["statement_chars_max"],
        )
        _require_instance(self.subject, MemorySubject, "subject")
        _require_instance(self.epistemic, MemoryEpistemic, "epistemic")
        _require_instance(self.temporal, MemoryTemporal, "temporal")
        _require_object_tuple(
            self.evidence_quotes,
            EvidenceQuote,
            "evidence_quotes",
            minimum=FIELD_LIMITS["evidence_count_min"],
            maximum=FIELD_LIMITS["evidence_count_max"],
        )
        _require_text(
            self.semantic_reason,
            "semantic_reason",
            minimum=FIELD_LIMITS["semantic_reason_chars_min"],
            maximum=FIELD_LIMITS["semantic_reason_chars_max"],
        )
        _require_number(
            self.confidence,
            "confidence",
            minimum=FIELD_LIMITS["confidence_min"],
            maximum=FIELD_LIMITS["confidence_max"],
        )
        _require_instance(
            self.relation_suggestions, MemoryRelations, "relation_suggestions"
        )


@dataclass(frozen=True, slots=True)
class MemoryRepresentation:
    schema_version: int
    memory_id: str
    version: int
    conversation_id: str
    proposal_run_id: str
    proposal_ordinal: int
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
        _require_integer(self.schema_version, "schema_version", minimum=1)
        if self.schema_version != MEMORY_SCHEMA_VERSION:
            raise MemoryContractError("schema_version must equal 1")
        _require_identifier(self.memory_id, "memory_id")
        _require_integer(
            self.version, "version", minimum=FIELD_LIMITS["version_min"]
        )
        _require_identifier(self.conversation_id, "conversation_id")
        _require_identifier(self.proposal_run_id, "proposal_run_id")
        _require_integer(
            self.proposal_ordinal,
            "proposal_ordinal",
            minimum=FIELD_LIMITS["proposal_ordinal_min"],
        )
        _require_enum(self.kind, MEMORY_KINDS, "kind")
        _require_text(
            self.statement,
            "statement",
            minimum=FIELD_LIMITS["statement_chars_min"],
            maximum=FIELD_LIMITS["statement_chars_max"],
        )
        _require_instance(self.subject, MemorySubject, "subject")
        _require_instance(self.epistemic, MemoryEpistemic, "epistemic")
        _require_instance(self.temporal, MemoryTemporal, "temporal")
        _require_optional_utc_rfc3339(self.temporal.start_at, "temporal.start_at")
        _require_optional_utc_rfc3339(self.temporal.end_at, "temporal.end_at")
        _require_object_tuple(
            self.evidence,
            MemoryEvidence,
            "evidence",
            minimum=FIELD_LIMITS["evidence_count_min"],
            maximum=FIELD_LIMITS["evidence_count_max"],
        )
        _require_text(
            self.semantic_reason,
            "semantic_reason",
            minimum=FIELD_LIMITS["semantic_reason_chars_min"],
            maximum=FIELD_LIMITS["semantic_reason_chars_max"],
        )
        _require_number(
            self.confidence,
            "confidence",
            minimum=FIELD_LIMITS["confidence_min"],
            maximum=FIELD_LIMITS["confidence_max"],
        )
        _require_instance(self.relations, MemoryRelations, "relations")
        _require_enum(self.status, MEMORY_STATUSES, "status")
        _require_utc_rfc3339(self.created_at, "created_at")
        _require_identifier(self.idempotency_key, "idempotency_key")


MemoryContract: TypeAlias = MemoryProposalDraft | MemoryRepresentation


def memory_proposal_draft_from_dict(value: object) -> MemoryProposalDraft:
    return _capture_parse_errors(lambda: _memory_proposal_draft_from_dict(value))


def _memory_proposal_draft_from_dict(value: object) -> MemoryProposalDraft:
    item = _require_object(value, DRAFT_FIELDS, "MemoryProposalDraft")
    return MemoryProposalDraft(
        kind=item["kind"],
        statement=item["statement"],
        subject=_subject_from_dict(item["subject"]),
        epistemic=_epistemic_from_dict(item["epistemic"]),
        temporal=_temporal_from_dict(item["temporal"]),
        evidence_quotes=_object_tuple_from_list(
            item["evidence_quotes"],
            _evidence_quote_from_dict,
            "evidence_quotes",
        ),
        semantic_reason=item["semantic_reason"],
        confidence=item["confidence"],
        relation_suggestions=_relations_from_dict(item["relation_suggestions"]),
    )


def memory_representation_from_dict(value: object) -> MemoryRepresentation:
    return _capture_parse_errors(lambda: _memory_representation_from_dict(value))


def _memory_representation_from_dict(value: object) -> MemoryRepresentation:
    item = _require_object(value, REPRESENTATION_FIELDS, "MemoryRepresentation")
    return MemoryRepresentation(
        schema_version=item["schema_version"],
        memory_id=item["memory_id"],
        version=item["version"],
        conversation_id=item["conversation_id"],
        proposal_run_id=item["proposal_run_id"],
        proposal_ordinal=item["proposal_ordinal"],
        kind=item["kind"],
        statement=item["statement"],
        subject=_subject_from_dict(item["subject"]),
        epistemic=_epistemic_from_dict(item["epistemic"]),
        temporal=_temporal_from_dict(item["temporal"]),
        evidence=_object_tuple_from_list(
            item["evidence"], _evidence_from_dict, "evidence"
        ),
        semantic_reason=item["semantic_reason"],
        confidence=item["confidence"],
        relations=_relations_from_dict(item["relations"]),
        status=item["status"],
        created_at=item["created_at"],
        idempotency_key=item["idempotency_key"],
    )


def memory_proposal_draft_from_json(value: str | bytes) -> MemoryProposalDraft:
    return memory_proposal_draft_from_dict(_decode_json_object(value))


def memory_representation_from_json(value: str | bytes) -> MemoryRepresentation:
    return memory_representation_from_dict(_decode_json_object(value))


def memory_contract_to_dict(value: MemoryContract) -> dict[str, Any]:
    if isinstance(value, MemoryProposalDraft):
        return {
            "kind": value.kind,
            "statement": value.statement,
            "subject": _subject_to_dict(value.subject),
            "epistemic": _epistemic_to_dict(value.epistemic),
            "temporal": _temporal_to_dict(value.temporal),
            "evidence_quotes": [
                _evidence_quote_to_dict(item) for item in value.evidence_quotes
            ],
            "semantic_reason": value.semantic_reason,
            "confidence": value.confidence,
            "relation_suggestions": _relations_to_dict(
                value.relation_suggestions
            ),
        }
    if isinstance(value, MemoryRepresentation):
        return {
            "schema_version": value.schema_version,
            "memory_id": value.memory_id,
            "version": value.version,
            "conversation_id": value.conversation_id,
            "proposal_run_id": value.proposal_run_id,
            "proposal_ordinal": value.proposal_ordinal,
            "kind": value.kind,
            "statement": value.statement,
            "subject": _subject_to_dict(value.subject),
            "epistemic": _epistemic_to_dict(value.epistemic),
            "temporal": _temporal_to_dict(value.temporal),
            "evidence": [_evidence_to_dict(item) for item in value.evidence],
            "semantic_reason": value.semantic_reason,
            "confidence": value.confidence,
            "relations": _relations_to_dict(value.relations),
            "status": value.status,
            "created_at": value.created_at,
            "idempotency_key": value.idempotency_key,
        }
    raise TypeError("value must be MemoryProposalDraft or MemoryRepresentation")


def canonical_memory_json(value: MemoryContract) -> bytes:
    return json.dumps(
        memory_contract_to_dict(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_memory_proposal_draft(value: MemoryProposalDraft) -> None:
    _require_instance(value, MemoryProposalDraft, "draft")
    _validate_subject_kind(value.kind, value.subject)
    _validate_temporal(value.kind, value.temporal)
    _validate_evidence_roles(value.evidence_quotes)
    _validate_relations(value.relation_suggestions, memory_id=None)


def validate_memory_representation(
    value: MemoryRepresentation,
    *,
    evidence_sources: Mapping[str, EvidenceSource] | None = None,
    relation_targets: Mapping[str, MemoryRepresentation] | None = None,
) -> None:
    _require_instance(value, MemoryRepresentation, "representation")
    _validate_subject_kind(value.kind, value.subject)
    _validate_temporal(value.kind, value.temporal)
    _validate_evidence_roles(value.evidence)
    _validate_relations(
        value.relations,
        memory_id=value.memory_id,
        conversation_id=value.conversation_id,
        relation_targets=relation_targets,
    )
    spans = set()
    for index, evidence in enumerate(value.evidence):
        path = f"evidence[{index}]"
        span = (evidence.event_id, evidence.excerpt_start, evidence.excerpt_end)
        if span in spans:
            _fail(
                "memory_cross_field_invalid",
                path,
                "duplicate evidence span",
            )
        spans.add(span)
        validate_evidence_excerpt(evidence)
    if evidence_sources is not None:
        _validate_evidence_sources(value, evidence_sources)


def validate_evidence_excerpt(
    evidence: MemoryEvidence, source_text: str | None = None
) -> None:
    _require_instance(evidence, MemoryEvidence, "evidence")
    if evidence.excerpt_end <= evidence.excerpt_start:
        _fail(
            "memory_evidence_excerpt_mismatch",
            "evidence.excerpt_end",
            "excerpt_end must be greater than excerpt_start",
        )
    if evidence.excerpt_end - evidence.excerpt_start != len(evidence.excerpt):
        _fail(
            "memory_evidence_excerpt_mismatch",
            "evidence.excerpt",
            "excerpt length does not match offsets",
        )
    expected_hash = hashlib.sha256(evidence.excerpt.encode("utf-8")).hexdigest()
    if evidence.excerpt_sha256 != expected_hash:
        _fail(
            "memory_evidence_excerpt_mismatch",
            "evidence.excerpt_sha256",
            "excerpt SHA256 does not match excerpt",
        )
    if source_text is not None:
        if type(source_text) is not str:
            raise TypeError("source_text must be a string")
        if evidence.excerpt_end > len(source_text):
            _fail(
                "memory_evidence_excerpt_mismatch",
                "evidence.excerpt_end",
                "excerpt range exceeds source text",
            )
        if source_text[evidence.excerpt_start : evidence.excerpt_end] != evidence.excerpt:
            _fail(
                "memory_evidence_excerpt_mismatch",
                "evidence.excerpt",
                "excerpt does not match source text at offsets",
            )


def validate_memory_transition(from_status: str | None, to_status: str) -> None:
    _require_enum(to_status, MEMORY_STATUSES, "to_status")
    if from_status is None:
        if to_status != INITIAL_MEMORY_STATUS:
            _fail(
                "memory_transition_invalid",
                "status",
                "new memory must start as proposed",
            )
        return
    _require_enum(from_status, MEMORY_STATUSES, "from_status")
    if to_status not in MEMORY_STATUS_TRANSITIONS[from_status]:
        _fail(
            "memory_transition_invalid",
            "status",
            f"invalid memory transition: {from_status} -> {to_status}",
        )


def validate_memory_idempotency(
    existing: MemoryRepresentation, candidate: MemoryRepresentation
) -> None:
    _require_instance(existing, MemoryRepresentation, "existing")
    _require_instance(candidate, MemoryRepresentation, "candidate")
    if existing.idempotency_key != candidate.idempotency_key:
        return
    if canonical_memory_json(existing) != canonical_memory_json(candidate):
        _fail(
            "memory_idempotency_conflict",
            "idempotency_key",
            "same idempotency key was reused with different content",
        )


def _validate_subject_kind(kind: str, subject: MemorySubject) -> None:
    if subject.subject_type == "third_party":
        if subject.entity_id is None:
            _fail(
                "memory_cross_field_invalid",
                "subject.entity_id",
                "third_party subject requires entity_id",
            )
    elif subject.entity_id is not None or subject.display_name is not None:
        _fail(
            "memory_cross_field_invalid",
            "subject",
            "built-in subject types cannot carry entity fields",
        )

    allowed_subjects = {
        "player_fact": {"player"},
        "preference_boundary": {"player", "relationship"},
        "person_relation": {"player", "character", "third_party"},
        "shared_experience": {"both"},
        "relationship_meaning": {"relationship"},
        "future_event": {"player", "character", "both", "third_party"},
        "unfinished_topic": set(SUBJECT_TYPES),
        "character_self_claim": {"character"},
    }
    if subject.subject_type not in allowed_subjects[kind]:
        _fail(
            "memory_cross_field_invalid",
            "subject.type",
            f"subject type is not valid for {kind}",
        )


def _validate_temporal(kind: str, temporal: MemoryTemporal) -> None:
    if temporal.timezone is not None:
        try:
            ZoneInfo(temporal.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise MemoryContractError(
                "temporal.timezone must be a valid IANA timezone",
                code="memory_cross_field_invalid",
                path="temporal.timezone",
            ) from error

    if temporal.end_at is not None and temporal.start_at is None:
        _fail(
            "memory_cross_field_invalid",
            "temporal.end_at",
            "end_at requires start_at",
        )
    if temporal.start_at is not None and temporal.end_at is not None:
        start = _parse_rfc3339(temporal.start_at)
        end = _parse_rfc3339(temporal.end_at)
        if end <= start:
            _fail(
                "memory_cross_field_invalid",
                "temporal.end_at",
                "end_at must be later than start_at",
            )

    if temporal.resolution == "not_applicable":
        if temporal.relation != "atemporal" or temporal.precision != "not_applicable":
            _fail(
                "memory_cross_field_invalid",
                "temporal",
                "not_applicable temporal value must be atemporal",
            )
        if any(
            item is not None
            for item in (
                temporal.source_text,
                temporal.anchor_event_id,
                temporal.start_at,
                temporal.end_at,
                temporal.timezone,
            )
        ):
            _fail(
                "memory_cross_field_invalid",
                "temporal",
                "not_applicable temporal value cannot carry time data",
            )
    elif temporal.resolution == "resolved":
        if temporal.start_at is None or temporal.timezone is None:
            _fail(
                "memory_cross_field_invalid",
                "temporal",
                "resolved temporal value requires start_at and timezone",
            )
        if temporal.precision in ("unknown", "not_applicable"):
            _fail(
                "memory_cross_field_invalid",
                "temporal.precision",
                "resolved temporal value requires known precision",
            )
    else:
        if temporal.source_text is None or temporal.anchor_event_id is None:
            _fail(
                "memory_cross_field_invalid",
                "temporal",
                "relative or ambiguous time requires source_text and anchor_event_id",
            )
        if temporal.start_at is not None or temporal.end_at is not None:
            _fail(
                "memory_cross_field_invalid",
                "temporal",
                "unresolved time cannot contain normalized timestamps",
            )

    if kind == "future_event" and temporal.relation != "future":
        _fail(
            "memory_cross_field_invalid",
            "temporal.relation",
            "future_event requires future temporal relation",
        )


def _validate_evidence_roles(
    evidence_items: tuple[EvidenceQuote, ...] | tuple[MemoryEvidence, ...]
) -> None:
    if not any(item.role in ("support", "correction") for item in evidence_items):
        _fail(
            "memory_evidence_missing",
            "evidence",
            "memory requires support or correction evidence",
        )


def _validate_evidence_sources(
    value: MemoryRepresentation,
    evidence_sources: Mapping[str, EvidenceSource],
) -> None:
    if not isinstance(evidence_sources, Mapping):
        raise TypeError("evidence_sources must be a mapping")
    supporting_actors = set()
    supporting_sources: list[EvidenceSource] = []
    for index, evidence in enumerate(value.evidence):
        path = f"evidence[{index}]"
        source = evidence_sources.get(evidence.event_id)
        if source is None:
            _fail(
                "memory_evidence_missing",
                f"{path}.event_id",
                "evidence source does not exist",
            )
        if source.event_id != evidence.event_id:
            _fail(
                "memory_schema_invalid",
                f"{path}.event_id",
                "evidence source mapping key does not match event_id",
            )
        if source.conversation_id != value.conversation_id:
            _fail(
                "memory_evidence_cross_conversation",
                f"{path}.event_id",
                "evidence belongs to another conversation",
            )
        if not source.committed or source.event_type in (
            "turn_failed",
            "generation_cancelled",
        ):
            _fail(
                "memory_evidence_uncommitted",
                f"{path}.event_id",
                "uncommitted or failed output cannot support memory",
            )
        if source.event_type not in ("message", "app_event", "offscreen_event"):
            _fail(
                "memory_evidence_uncommitted",
                f"{path}.event_id",
                "event type is not admissible memory evidence",
            )
        validate_evidence_excerpt(evidence, source.text)
        if evidence.role in ("support", "correction"):
            supporting_actors.add(source.actor)
            supporting_sources.append(source)

    if value.epistemic.grounding in ("acknowledged", "mutually_confirmed"):
        if not {"user", "character"}.issubset(supporting_actors):
            _fail(
                "memory_evidence_missing",
                "epistemic.grounding",
                "acknowledged or mutually_confirmed requires user and character support",
            )
    if value.epistemic.grounding == "runtime_observed":
        if not any(
            source.actor == "system"
            and source.event_type in ("app_event", "offscreen_event")
            for source in supporting_sources
        ):
            _fail(
                "memory_evidence_missing",
                "epistemic.grounding",
                "runtime_observed requires committed runtime evidence",
            )
    if value.kind == "player_fact" and not any(
        source.actor == "user" for source in supporting_sources
    ):
        _fail(
            "memory_evidence_missing",
            "evidence",
            "player_fact requires user support evidence",
        )
    if value.kind == "character_self_claim" and not any(
        (source.actor == "character" and source.event_type == "message")
        or (source.actor == "system" and source.event_type == "offscreen_event")
        for source in supporting_sources
    ):
        _fail(
            "memory_evidence_missing",
            "evidence",
            "character_self_claim requires character or offscreen evidence",
        )


def _validate_relations(
    relations: MemoryRelations,
    *,
    memory_id: str | None,
    conversation_id: str | None = None,
    relation_targets: Mapping[str, MemoryRepresentation] | None = None,
) -> None:
    groups = {
        "supersedes": set(relations.supersedes),
        "contradicts": set(relations.contradicts),
        "refines": set(relations.refines),
    }
    if memory_id is not None and any(memory_id in group for group in groups.values()):
        _fail(
            "memory_cross_field_invalid",
            "relations",
            "memory cannot relate to itself",
        )
    if (
        groups["supersedes"] & groups["contradicts"]
        or groups["supersedes"] & groups["refines"]
        or groups["contradicts"] & groups["refines"]
    ):
        _fail(
            "memory_cross_field_invalid",
            "relations",
            "same target cannot have multiple relation types",
        )
    if relation_targets is None:
        return
    if not isinstance(relation_targets, Mapping):
        raise TypeError("relation_targets must be a mapping")
    for relation_name, targets in groups.items():
        for target_id in targets:
            target = relation_targets.get(target_id)
            if target is None:
                _fail(
                    "memory_relation_missing_target",
                    f"relations.{relation_name}",
                    "relation target does not exist",
                )
            if target.memory_id != target_id:
                _fail(
                    "memory_schema_invalid",
                    f"relations.{relation_name}",
                    "relation target mapping key does not match memory_id",
                )
            if target.conversation_id != conversation_id:
                _fail(
                    "memory_relation_cross_conversation",
                    f"relations.{relation_name}",
                    "relation target belongs to another conversation",
                )


def _parse_rfc3339(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fail(code: str, path: str, message: str) -> None:
    raise MemoryContractError(message, code=code, path=path)


def _capture_parse_errors(builder: Callable[[], Any]) -> Any:
    try:
        return builder()
    except MemoryContractError:
        raise
    except TypeError as error:
        raise MemoryContractError(
            str(error), code="memory_schema_invalid"
        ) from error


def _subject_from_dict(value: object) -> MemorySubject:
    item = _require_object(value, SUBJECT_FIELDS, "subject")
    return MemorySubject(
        subject_type=item["type"],
        entity_id=item["entity_id"],
        display_name=item["display_name"],
    )


def _epistemic_from_dict(value: object) -> MemoryEpistemic:
    item = _require_object(value, EPISTEMIC_FIELDS, "epistemic")
    return MemoryEpistemic(
        polarity=item["polarity"],
        modality=item["modality"],
        grounding=item["grounding"],
    )


def _temporal_from_dict(value: object) -> MemoryTemporal:
    item = _require_object(value, TEMPORAL_FIELDS, "temporal")
    return MemoryTemporal(
        relation=item["relation"],
        resolution=item["resolution"],
        source_text=item["source_text"],
        anchor_event_id=item["anchor_event_id"],
        start_at=item["start_at"],
        end_at=item["end_at"],
        timezone=item["timezone"],
        precision=item["precision"],
    )


def _evidence_quote_from_dict(value: object) -> EvidenceQuote:
    item = _require_object(value, EVIDENCE_QUOTE_FIELDS, "evidence_quote")
    return EvidenceQuote(
        event_id=item["event_id"],
        role=item["role"],
        quote=item["quote"],
        start_hint=item["start_hint"],
    )


def _evidence_from_dict(value: object) -> MemoryEvidence:
    item = _require_object(value, EVIDENCE_FIELDS, "evidence")
    return MemoryEvidence(
        event_id=item["event_id"],
        role=item["role"],
        excerpt_start=item["excerpt_start"],
        excerpt_end=item["excerpt_end"],
        excerpt=item["excerpt"],
        excerpt_sha256=item["excerpt_sha256"],
    )


def _relations_from_dict(value: object) -> MemoryRelations:
    item = _require_object(value, RELATION_FIELDS, "relations")
    return MemoryRelations(
        semantic_slot=item["semantic_slot"],
        supersedes=_identifier_tuple_from_list(
            item["supersedes"], "relations.supersedes"
        ),
        contradicts=_identifier_tuple_from_list(
            item["contradicts"], "relations.contradicts"
        ),
        refines=_identifier_tuple_from_list(item["refines"], "relations.refines"),
    )


def _subject_to_dict(value: MemorySubject) -> dict[str, Any]:
    return {
        "type": value.subject_type,
        "entity_id": value.entity_id,
        "display_name": value.display_name,
    }


def _epistemic_to_dict(value: MemoryEpistemic) -> dict[str, Any]:
    return {
        "polarity": value.polarity,
        "modality": value.modality,
        "grounding": value.grounding,
    }


def _temporal_to_dict(value: MemoryTemporal) -> dict[str, Any]:
    return {
        "relation": value.relation,
        "resolution": value.resolution,
        "source_text": value.source_text,
        "anchor_event_id": value.anchor_event_id,
        "start_at": value.start_at,
        "end_at": value.end_at,
        "timezone": value.timezone,
        "precision": value.precision,
    }


def _evidence_quote_to_dict(value: EvidenceQuote) -> dict[str, Any]:
    return {
        "event_id": value.event_id,
        "role": value.role,
        "quote": value.quote,
        "start_hint": value.start_hint,
    }


def _evidence_to_dict(value: MemoryEvidence) -> dict[str, Any]:
    return {
        "event_id": value.event_id,
        "role": value.role,
        "excerpt_start": value.excerpt_start,
        "excerpt_end": value.excerpt_end,
        "excerpt": value.excerpt,
        "excerpt_sha256": value.excerpt_sha256,
    }


def _relations_to_dict(value: MemoryRelations) -> dict[str, Any]:
    return {
        "semantic_slot": value.semantic_slot,
        "supersedes": list(value.supersedes),
        "contradicts": list(value.contradicts),
        "refines": list(value.refines),
    }


def _require_object(
    value: object, expected_fields: tuple[str, ...], name: str
) -> dict[str, Any]:
    if type(value) is not dict:
        raise TypeError(f"{name} must be an object")
    if any(type(key) is not str for key in value):
        raise TypeError(f"{name} keys must be strings")
    expected = set(expected_fields)
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unknown:
            details.append(f"unknown={','.join(unknown)}")
        raise MemoryContractError(
            f"{name} fields invalid: {'; '.join(details)}",
            code="memory_unknown_field" if unknown else "memory_schema_invalid",
            path=name,
        )
    return value


def _object_tuple_from_list(
    value: object,
    parser: Any,
    name: str,
) -> tuple[Any, ...]:
    if type(value) is not list:
        raise TypeError(f"{name} must be an array")
    return tuple(parser(item) for item in value)


def _identifier_tuple_from_list(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise TypeError(f"{name} must be an array")
    result = tuple(value)
    _require_identifier_tuple(
        result,
        name,
        maximum=FIELD_LIMITS["relation_targets_per_type_max"],
    )
    return result


def _require_object_tuple(
    value: object,
    item_type: type,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if not minimum <= len(value) <= maximum:
        raise MemoryContractError(f"{name} count is outside the frozen limits")
    if any(type(item) is not item_type for item in value):
        raise TypeError(f"{name} contains an invalid item type")
    if len(value) != len(set(value)):
        raise MemoryContractError(f"{name} must contain unique items")


def _require_identifier_tuple(value: object, name: str, *, maximum: int) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{name} must be a tuple")
    if len(value) > maximum:
        raise MemoryContractError(f"{name} exceeds the frozen limit")
    for item in value:
        _require_identifier(item, f"{name} item")
    if len(value) != len(set(value)):
        raise MemoryContractError(f"{name} must contain unique IDs")


def _require_instance(value: object, expected: type, name: str) -> None:
    if type(value) is not expected:
        raise TypeError(f"{name} must be {expected.__name__}")


def _require_enum(value: object, allowed: tuple[str, ...], name: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if value not in allowed:
        raise MemoryContractError(
            f"{name} is not a frozen enum value",
            code="memory_invalid_enum",
            path=name,
        )


def _require_text(
    value: object,
    name: str,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if len(value) < minimum or (maximum is not None and len(value) > maximum):
        raise MemoryContractError(f"{name} length is outside the frozen limits")


def _require_optional_text(
    value: object, name: str, *, maximum: int
) -> None:
    if value is None:
        return
    _require_text(value, name, maximum=maximum)


def _require_identifier(value: object, name: str) -> None:
    _require_text(
        value, name, maximum=FIELD_LIMITS["identifier_chars_max"]
    )
    if any(character.isspace() for character in value):
        raise MemoryContractError(f"{name} cannot contain whitespace")


def _require_optional_identifier(value: object, name: str) -> None:
    if value is None:
        return
    _require_identifier(value, name)


def _require_integer(value: object, name: str, *, minimum: int) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise MemoryContractError(f"{name} is below the frozen minimum")


def _require_optional_integer(
    value: object, name: str, *, minimum: int
) -> None:
    if value is None:
        return
    _require_integer(value, name, minimum=minimum)


def _require_number(
    value: object,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> None:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value):
        raise MemoryContractError(f"{name} must be finite")
    if not minimum <= value <= maximum:
        raise MemoryContractError(f"{name} is outside the frozen range")


def _require_rfc3339(value: object, name: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not _RFC3339_PATTERN.fullmatch(value):
        raise MemoryContractError(f"{name} must be RFC3339 date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MemoryContractError(f"{name} must be RFC3339 date-time") from error
    if parsed.utcoffset() is None:
        raise MemoryContractError(f"{name} must include a timezone")


def _require_optional_rfc3339(value: object, name: str) -> None:
    if value is None:
        return
    _require_rfc3339(value, name)


def _require_utc_rfc3339(value: object, name: str) -> None:
    _require_rfc3339(value, name)
    if not value.endswith("Z"):
        raise MemoryContractError(f"{name} must be normalized to UTC Z")


def _require_optional_utc_rfc3339(value: object, name: str) -> None:
    if value is None:
        return
    _require_utc_rfc3339(value, name)


def _decode_json_object(value: str | bytes) -> dict[str, Any]:
    if type(value) not in (str, bytes):
        raise TypeError("JSON input must be str or bytes")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise MemoryContractError(f"duplicate JSON field: {key}")
            result[key] = item
        return result

    def reject_constant(constant: str) -> None:
        raise MemoryContractError(f"non-finite JSON number is forbidden: {constant}")

    try:
        decoded = json.loads(
            value,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MemoryContractError("invalid JSON") from error
    if type(decoded) is not dict:
        raise TypeError("memory JSON root must be an object")
    return decoded
