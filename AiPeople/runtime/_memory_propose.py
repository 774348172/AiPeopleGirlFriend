from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ._memory_contracts import (
    MEMORY_INDEXABLE_STATUSES,
    MEMORY_KINDS,
    MemoryProposalDraft,
    MemorySubject,
    canonical_memory_json,
    memory_contract_to_dict,
    memory_proposal_draft_from_dict,
    validate_memory_proposal_draft,
)


MEMORY_PROPOSE_SCHEMA_VERSION: Final = 1
MEMORY_PROPOSE_MODE: Final = "MEMORY_PROPOSE"
MEMORY_PROPOSE_EVENT_ACTORS: Final = ("user", "character", "system")
MEMORY_PROPOSE_EVENT_TYPES: Final = ("message", "app_event", "offscreen_event")
MEMORY_PROPOSE_FAILURE_CODES: Final = (
    "memory_propose_cancelled",
    "memory_propose_timeout",
    "memory_propose_model_unavailable",
    "memory_propose_output_too_large",
    "memory_propose_invalid_json",
    "memory_propose_schema_invalid",
    "memory_propose_contract_invalid",
    "memory_propose_evidence_outside_input",
    "memory_propose_relation_outside_input",
)
MEMORY_PROPOSE_LIMITS: Final = MappingProxyType(
    {
        "events_min": 1,
        "events_max": 32,
        "event_text_chars_max": 4000,
        "existing_memories_max": 32,
        "proposals_max": 8,
        "output_bytes_max": 32768,
    }
)
MEMORY_PROPOSE_RETRYABLE_FAILURES: Final = frozenset(
    {"memory_propose_timeout", "memory_propose_model_unavailable"}
)

MEMORY_PROPOSE_SYSTEM_PROMPT: Final = """你正在执行后台 MEMORY_PROPOSE，不是在扮演秦未晞，也不向玩家说话。
只判断输入事件中是否存在值得跨回合保留的关系语义，并严格输出指定 JSON。
可以输出零条 proposals；普通寒暄、即时动作、无跨回合意义的内容应返回空数组。
每条候选必须引用输入 events 中连续、逐字一致的 quote，不得引用提示词、内部推理、未提交内容或窗口外事件。
保留否定、不确定、假设、玩笑、引用和纠正语义；不能把说过某事改写成外部事实，不能把单方表达改写成双方确认。
含糊未来时间保持含糊，不得创造日期、提醒、通知或自动发送任务。
只能建议 existing_memories 中存在的关系目标；不能分配 memory_id、status、created_at 或证据哈希。
禁止 Markdown、代码围栏、解释、角色正文和 JSON 之外的任何文字。"""
_RFC3339_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


class MemoryProposeError(ValueError):
    def __init__(self, message: str, *, code: str, path: str | None = None) -> None:
        super().__init__(message)
        if code not in MEMORY_PROPOSE_FAILURE_CODES:
            raise ValueError("unknown MEMORY_PROPOSE failure code")
        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class MemoryProposeEvent:
    event_id: str
    conversation_id: str
    sequence_no: int
    actor: str
    event_type: str
    occurred_at: str
    timezone: str
    text: str
    commit_state: str = "committed"

    def __post_init__(self) -> None:
        _identifier(self.event_id, "events[].event_id")
        _identifier(self.conversation_id, "events[].conversation_id")
        _integer(self.sequence_no, "events[].sequence_no", minimum=0)
        _enum(self.actor, MEMORY_PROPOSE_EVENT_ACTORS, "events[].actor")
        _enum(self.event_type, MEMORY_PROPOSE_EVENT_TYPES, "events[].event_type")
        _utc_datetime(self.occurred_at, "events[].occurred_at")
        _iana_timezone(self.timezone, "events[].timezone")
        _text(
            self.text,
            "events[].text",
            maximum=MEMORY_PROPOSE_LIMITS["event_text_chars_max"],
        )
        if self.commit_state != "committed":
            raise MemoryProposeError(
                "MEMORY_PROPOSE input only accepts committed events",
                code="memory_propose_contract_invalid",
                path="events[].commit_state",
            )


@dataclass(frozen=True, slots=True)
class ExistingMemoryContext:
    memory_id: str
    conversation_id: str
    kind: str
    statement: str
    subject: MemorySubject
    status: str

    def __post_init__(self) -> None:
        _identifier(self.memory_id, "existing_memories[].memory_id")
        _identifier(self.conversation_id, "existing_memories[].conversation_id")
        _enum(self.kind, MEMORY_KINDS, "existing_memories[].kind")
        _text(self.statement, "existing_memories[].statement", maximum=500)
        if type(self.subject) is not MemorySubject:
            raise TypeError("existing_memories[].subject must be MemorySubject")
        _enum(
            self.status,
            MEMORY_INDEXABLE_STATUSES,
            "existing_memories[].status",
        )
        _validate_existing_subject(self.kind, self.subject)


@dataclass(frozen=True, slots=True)
class MemoryProposeRequest:
    schema_version: int
    mode: str
    proposal_run_id: str
    conversation_id: str
    from_sequence_no: int
    through_sequence_no: int
    events: tuple[MemoryProposeEvent, ...]
    existing_memories: tuple[ExistingMemoryContext, ...]
    max_proposals: int

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_PROPOSE_SCHEMA_VERSION:
            raise MemoryProposeError(
                "unsupported MEMORY_PROPOSE schema version",
                code="memory_propose_schema_invalid",
                path="schema_version",
            )
        if self.mode != MEMORY_PROPOSE_MODE:
            raise MemoryProposeError(
                "mode must be MEMORY_PROPOSE",
                code="memory_propose_schema_invalid",
                path="mode",
            )
        _identifier(self.proposal_run_id, "proposal_run_id")
        _identifier(self.conversation_id, "conversation_id")
        _integer(self.from_sequence_no, "from_sequence_no", minimum=0)
        _integer(self.through_sequence_no, "through_sequence_no", minimum=0)
        _tuple_of(
            self.events,
            MemoryProposeEvent,
            "events",
            minimum=MEMORY_PROPOSE_LIMITS["events_min"],
            maximum=MEMORY_PROPOSE_LIMITS["events_max"],
        )
        _tuple_of(
            self.existing_memories,
            ExistingMemoryContext,
            "existing_memories",
            minimum=0,
            maximum=MEMORY_PROPOSE_LIMITS["existing_memories_max"],
        )
        if self.max_proposals != MEMORY_PROPOSE_LIMITS["proposals_max"]:
            raise MemoryProposeError(
                "max_proposals must equal the frozen limit",
                code="memory_propose_contract_invalid",
                path="max_proposals",
            )
        _validate_request_window(self)


@dataclass(frozen=True, slots=True)
class MemoryProposeResult:
    schema_version: int
    mode: str
    proposal_run_id: str
    proposals: tuple[MemoryProposalDraft, ...]

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_PROPOSE_SCHEMA_VERSION:
            raise MemoryProposeError(
                "unsupported MEMORY_PROPOSE result schema version",
                code="memory_propose_schema_invalid",
                path="schema_version",
            )
        if self.mode != MEMORY_PROPOSE_MODE:
            raise MemoryProposeError(
                "result mode must be MEMORY_PROPOSE",
                code="memory_propose_schema_invalid",
                path="mode",
            )
        _identifier(self.proposal_run_id, "proposal_run_id")
        _tuple_of(
            self.proposals,
            MemoryProposalDraft,
            "proposals",
            minimum=0,
            maximum=MEMORY_PROPOSE_LIMITS["proposals_max"],
        )


@dataclass(frozen=True, slots=True)
class MemoryProposeFailure:
    proposal_run_id: str
    code: str
    retryable: bool

    def __post_init__(self) -> None:
        _identifier(self.proposal_run_id, "proposal_run_id")
        _enum(self.code, MEMORY_PROPOSE_FAILURE_CODES, "code")
        if type(self.retryable) is not bool:
            raise TypeError("retryable must be a bool")
        expected = self.code in MEMORY_PROPOSE_RETRYABLE_FAILURES
        if self.retryable != expected:
            raise MemoryProposeError(
                "retryable does not match the frozen failure policy",
                code="memory_propose_contract_invalid",
                path="retryable",
            )


MemoryProposeOutcome: TypeAlias = MemoryProposeResult | MemoryProposeFailure


def memory_propose_request_from_dict(value: object) -> MemoryProposeRequest:
    item = _object(
        value,
        (
            "schema_version",
            "mode",
            "proposal_run_id",
            "conversation_id",
            "from_sequence_no",
            "through_sequence_no",
            "events",
            "existing_memories",
            "max_proposals",
        ),
        "MemoryProposeRequest",
    )
    try:
        events = _array(item["events"], "events")
        existing = _array(item["existing_memories"], "existing_memories")
        return MemoryProposeRequest(
            schema_version=item["schema_version"],
            mode=item["mode"],
            proposal_run_id=item["proposal_run_id"],
            conversation_id=item["conversation_id"],
            from_sequence_no=item["from_sequence_no"],
            through_sequence_no=item["through_sequence_no"],
            events=tuple(_event_from_dict(event) for event in events),
            existing_memories=tuple(
                _existing_memory_from_dict(memory) for memory in existing
            ),
            max_proposals=item["max_proposals"],
        )
    except MemoryProposeError:
        raise
    except (TypeError, ValueError) as error:
        raise MemoryProposeError(
            str(error), code="memory_propose_schema_invalid"
        ) from error


def memory_propose_request_to_dict(value: MemoryProposeRequest) -> dict[str, Any]:
    if type(value) is not MemoryProposeRequest:
        raise TypeError("value must be MemoryProposeRequest")
    return {
        "schema_version": value.schema_version,
        "mode": value.mode,
        "proposal_run_id": value.proposal_run_id,
        "conversation_id": value.conversation_id,
        "from_sequence_no": value.from_sequence_no,
        "through_sequence_no": value.through_sequence_no,
        "events": [_event_to_dict(event) for event in value.events],
        "existing_memories": [
            _existing_memory_to_dict(memory) for memory in value.existing_memories
        ],
        "max_proposals": value.max_proposals,
    }


def memory_propose_result_to_dict(value: MemoryProposeResult) -> dict[str, Any]:
    if type(value) is not MemoryProposeResult:
        raise TypeError("value must be MemoryProposeResult")
    return {
        "schema_version": value.schema_version,
        "mode": value.mode,
        "proposal_run_id": value.proposal_run_id,
        "proposals": [memory_contract_to_dict(item) for item in value.proposals],
    }


def canonical_memory_propose_request_json(value: MemoryProposeRequest) -> bytes:
    return _canonical_json(memory_propose_request_to_dict(value))


def canonical_memory_propose_result_json(value: MemoryProposeResult) -> bytes:
    return _canonical_json(memory_propose_result_to_dict(value))


def build_memory_propose_messages(
    value: MemoryProposeRequest,
) -> tuple[dict[str, str], dict[str, str]]:
    return (
        {"role": "system", "content": MEMORY_PROPOSE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": canonical_memory_propose_request_json(value).decode("utf-8"),
        },
    )


def parse_memory_propose_output(
    raw: str | bytes, request: MemoryProposeRequest
) -> MemoryProposeResult:
    if type(request) is not MemoryProposeRequest:
        raise TypeError("request must be MemoryProposeRequest")
    if type(raw) not in (str, bytes):
        raise TypeError("MEMORY_PROPOSE output must be str or bytes")
    try:
        raw_bytes = raw.encode("utf-8") if type(raw) is str else raw
    except UnicodeEncodeError as error:
        raise MemoryProposeError(
            "MEMORY_PROPOSE output is not valid Unicode",
            code="memory_propose_invalid_json",
        ) from error
    if len(raw_bytes) > MEMORY_PROPOSE_LIMITS["output_bytes_max"]:
        raise MemoryProposeError(
            "MEMORY_PROPOSE output exceeds frozen byte limit",
            code="memory_propose_output_too_large",
        )
    decoded = _decode_json(raw_bytes)
    item = _object(
        decoded,
        ("schema_version", "mode", "proposal_run_id", "proposals"),
        "MemoryProposeResult",
    )
    try:
        raw_proposals = _array(item["proposals"], "proposals")
        proposals = tuple(
            memory_proposal_draft_from_dict(proposal) for proposal in raw_proposals
        )
        result = MemoryProposeResult(
            schema_version=item["schema_version"],
            mode=item["mode"],
            proposal_run_id=item["proposal_run_id"],
            proposals=proposals,
        )
    except MemoryProposeError:
        raise
    except (TypeError, ValueError) as error:
        raise MemoryProposeError(
            str(error), code="memory_propose_schema_invalid"
        ) from error
    validate_memory_propose_result(result, request)
    return result


def validate_memory_propose_result(
    result: MemoryProposeResult, request: MemoryProposeRequest
) -> None:
    if result.proposal_run_id != request.proposal_run_id:
        raise MemoryProposeError(
            "result proposal_run_id does not match request",
            code="memory_propose_contract_invalid",
            path="proposal_run_id",
        )
    event_map = {event.event_id: event for event in request.events}
    existing_ids = {memory.memory_id for memory in request.existing_memories}
    seen = set()
    for proposal_index, proposal in enumerate(result.proposals):
        try:
            validate_memory_proposal_draft(proposal)
        except (TypeError, ValueError) as error:
            raise MemoryProposeError(
                str(error),
                code="memory_propose_contract_invalid",
                path=f"proposals[{proposal_index}]",
            ) from error
        canonical = canonical_memory_json(proposal)
        if canonical in seen:
            raise MemoryProposeError(
                "duplicate proposal in one result",
                code="memory_propose_contract_invalid",
                path=f"proposals[{proposal_index}]",
            )
        seen.add(canonical)
        for quote_index, quote in enumerate(proposal.evidence_quotes):
            event = event_map.get(quote.event_id)
            if event is None:
                raise MemoryProposeError(
                    "proposal evidence references an event outside the input window",
                    code="memory_propose_evidence_outside_input",
                    path=f"proposals[{proposal_index}].evidence_quotes[{quote_index}].event_id",
                )
            if quote.start_hint is None:
                matches = event.text.count(quote.quote)
                if matches == 0:
                    raise MemoryProposeError(
                        "proposal quote is not a contiguous input excerpt",
                        code="memory_propose_evidence_outside_input",
                        path=f"proposals[{proposal_index}].evidence_quotes[{quote_index}].quote",
                    )
            else:
                end = quote.start_hint + len(quote.quote)
                if event.text[quote.start_hint:end] != quote.quote:
                    raise MemoryProposeError(
                        "proposal quote does not match start_hint",
                        code="memory_propose_evidence_outside_input",
                        path=f"proposals[{proposal_index}].evidence_quotes[{quote_index}].start_hint",
                    )
        anchor = proposal.temporal.anchor_event_id
        if anchor is not None and anchor not in event_map:
            raise MemoryProposeError(
                "temporal anchor references an event outside the input window",
                code="memory_propose_evidence_outside_input",
                path=f"proposals[{proposal_index}].temporal.anchor_event_id",
            )
        for relation_name in ("supersedes", "contradicts", "refines"):
            for target in getattr(proposal.relation_suggestions, relation_name):
                if target not in existing_ids:
                    raise MemoryProposeError(
                        "relation target is outside existing_memories",
                        code="memory_propose_relation_outside_input",
                        path=f"proposals[{proposal_index}].relation_suggestions.{relation_name}",
                    )


def memory_propose_failure(proposal_run_id: str, code: str) -> MemoryProposeFailure:
    return MemoryProposeFailure(
        proposal_run_id=proposal_run_id,
        code=code,
        retryable=code in MEMORY_PROPOSE_RETRYABLE_FAILURES,
    )


def _validate_request_window(value: MemoryProposeRequest) -> None:
    sequence_numbers = tuple(event.sequence_no for event in value.events)
    if sequence_numbers != tuple(sorted(sequence_numbers)) or len(sequence_numbers) != len(
        set(sequence_numbers)
    ):
        raise MemoryProposeError(
            "events must have unique ascending sequence numbers",
            code="memory_propose_contract_invalid",
            path="events",
        )
    if value.from_sequence_no != sequence_numbers[0] or value.through_sequence_no != sequence_numbers[-1]:
        raise MemoryProposeError(
            "sequence range must match the first and last event",
            code="memory_propose_contract_invalid",
            path="from_sequence_no",
        )
    event_ids = tuple(event.event_id for event in value.events)
    if len(event_ids) != len(set(event_ids)):
        raise MemoryProposeError(
            "event IDs must be unique",
            code="memory_propose_contract_invalid",
            path="events",
        )
    if any(event.conversation_id != value.conversation_id for event in value.events):
        raise MemoryProposeError(
            "all events must belong to the request conversation",
            code="memory_propose_contract_invalid",
            path="events[].conversation_id",
        )
    memory_ids = tuple(memory.memory_id for memory in value.existing_memories)
    if len(memory_ids) != len(set(memory_ids)):
        raise MemoryProposeError(
            "existing memory IDs must be unique",
            code="memory_propose_contract_invalid",
            path="existing_memories",
        )
    if any(
        memory.conversation_id != value.conversation_id
        for memory in value.existing_memories
    ):
        raise MemoryProposeError(
            "all existing memories must belong to the request conversation",
            code="memory_propose_contract_invalid",
            path="existing_memories[].conversation_id",
        )


def _validate_existing_subject(kind: str, subject: MemorySubject) -> None:
    if subject.subject_type == "third_party":
        if subject.entity_id is None:
            raise MemoryProposeError(
                "third_party memory context requires entity_id",
                code="memory_propose_contract_invalid",
                path="existing_memories[].subject.entity_id",
            )
    elif subject.entity_id is not None or subject.display_name is not None:
        raise MemoryProposeError(
            "built-in subjects cannot carry entity fields",
            code="memory_propose_contract_invalid",
            path="existing_memories[].subject",
        )
    allowed = {
        "player_fact": {"player"},
        "preference_boundary": {"player", "relationship"},
        "person_relation": {"player", "character", "third_party"},
        "shared_experience": {"both"},
        "relationship_meaning": {"relationship"},
        "future_event": {"player", "character", "both", "third_party"},
        "unfinished_topic": {"player", "character", "both", "relationship", "third_party"},
        "character_self_claim": {"character"},
    }
    if subject.subject_type not in allowed[kind]:
        raise MemoryProposeError(
            "memory context subject is invalid for kind",
            code="memory_propose_contract_invalid",
            path="existing_memories[].subject.type",
        )


def _event_from_dict(value: object) -> MemoryProposeEvent:
    item = _object(
        value,
        (
            "event_id",
            "conversation_id",
            "sequence_no",
            "actor",
            "event_type",
            "occurred_at",
            "timezone",
            "text",
            "commit_state",
        ),
        "MemoryProposeEvent",
    )
    return MemoryProposeEvent(**item)


def _existing_memory_from_dict(value: object) -> ExistingMemoryContext:
    item = _object(
        value,
        ("memory_id", "conversation_id", "kind", "statement", "subject", "status"),
        "ExistingMemoryContext",
    )
    subject = _object(item["subject"], ("type", "entity_id", "display_name"), "subject")
    return ExistingMemoryContext(
        memory_id=item["memory_id"],
        conversation_id=item["conversation_id"],
        kind=item["kind"],
        statement=item["statement"],
        subject=MemorySubject(
            subject_type=subject["type"],
            entity_id=subject["entity_id"],
            display_name=subject["display_name"],
        ),
        status=item["status"],
    )


def _event_to_dict(value: MemoryProposeEvent) -> dict[str, Any]:
    return {
        "event_id": value.event_id,
        "conversation_id": value.conversation_id,
        "sequence_no": value.sequence_no,
        "actor": value.actor,
        "event_type": value.event_type,
        "occurred_at": value.occurred_at,
        "timezone": value.timezone,
        "text": value.text,
        "commit_state": value.commit_state,
    }


def _existing_memory_to_dict(value: ExistingMemoryContext) -> dict[str, Any]:
    return {
        "memory_id": value.memory_id,
        "conversation_id": value.conversation_id,
        "kind": value.kind,
        "statement": value.statement,
        "subject": {
            "type": value.subject.subject_type,
            "entity_id": value.subject.entity_id,
            "display_name": value.subject.display_name,
        },
        "status": value.status,
    }


def _decode_json(raw: bytes) -> dict[str, Any]:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise MemoryProposeError(
                    "duplicate JSON field",
                    code="memory_propose_invalid_json",
                    path=key,
                )
            result[key] = value
        return result

    try:
        decoded = json.loads(raw, object_pairs_hook=pairs_hook, parse_constant=_reject_constant)
    except MemoryProposeError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MemoryProposeError(
            "MEMORY_PROPOSE output is not valid JSON",
            code="memory_propose_invalid_json",
        ) from error
    if type(decoded) is not dict:
        raise MemoryProposeError(
            "MEMORY_PROPOSE output root must be an object",
            code="memory_propose_schema_invalid",
        )
    return decoded


def _reject_constant(_value: str) -> None:
    raise MemoryProposeError(
        "non-finite JSON number is forbidden",
        code="memory_propose_invalid_json",
    )


def _object(value: object, fields: tuple[str, ...], name: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise MemoryProposeError(
            f"{name} must be an object with string keys",
            code="memory_propose_schema_invalid",
        )
    expected = set(fields)
    actual = set(value)
    if expected != actual:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        details = f"missing={missing}; unknown={unknown}"
        raise MemoryProposeError(
            f"{name} fields invalid: {details}",
            code="memory_propose_schema_invalid",
        )
    return value


def _array(value: object, name: str) -> list[Any]:
    if type(value) is not list:
        raise MemoryProposeError(
            f"{name} must be an array", code="memory_propose_schema_invalid", path=name
        )
    return value


def _tuple_of(
    value: object,
    item_type: type,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if type(value) is not tuple or any(type(item) is not item_type for item in value):
        raise TypeError(f"{name} must be a tuple of {item_type.__name__}")
    if not minimum <= len(value) <= maximum:
        raise MemoryProposeError(
            f"{name} count is outside frozen limits",
            code="memory_propose_schema_invalid",
            path=name,
        )


def _identifier(value: object, name: str) -> None:
    _text(value, name, maximum=256)
    if any(character.isspace() for character in value):
        raise MemoryProposeError(
            f"{name} cannot contain whitespace",
            code="memory_propose_schema_invalid",
            path=name,
        )


def _text(value: object, name: str, *, maximum: int) -> None:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise MemoryProposeError(
            f"{name} must be a non-empty bounded string",
            code="memory_propose_schema_invalid",
            path=name,
        )


def _integer(value: object, name: str, *, minimum: int) -> None:
    if type(value) is not int or value < minimum:
        raise MemoryProposeError(
            f"{name} must be an integer at or above {minimum}",
            code="memory_propose_schema_invalid",
            path=name,
        )


def _enum(value: object, allowed: tuple[str, ...], name: str) -> None:
    if type(value) is not str or value not in allowed:
        raise MemoryProposeError(
            f"{name} is not a frozen enum value",
            code="memory_propose_schema_invalid",
            path=name,
        )


def _utc_datetime(value: object, name: str) -> None:
    if type(value) is not str or not _RFC3339_UTC_PATTERN.fullmatch(value):
        raise MemoryProposeError(
            f"{name} must be RFC3339 UTC Z",
            code="memory_propose_schema_invalid",
            path=name,
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MemoryProposeError(
            f"{name} must be RFC3339 UTC Z",
            code="memory_propose_schema_invalid",
            path=name,
        ) from error
    if parsed.utcoffset() is None:
        raise MemoryProposeError(
            f"{name} must include timezone",
            code="memory_propose_schema_invalid",
            path=name,
        )


def _iana_timezone(value: object, name: str) -> None:
    _text(value, name, maximum=128)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise MemoryProposeError(
            f"{name} must be a valid IANA timezone",
            code="memory_propose_schema_invalid",
            path=name,
        ) from error


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
