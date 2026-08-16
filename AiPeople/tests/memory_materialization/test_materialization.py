from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from runtime._ledger import EventRecord
from runtime._memory_materialization import (
    MemoryMaterializationError,
    materialize_memory_proposals,
)
from runtime._memory_propose import (
    memory_propose_request_from_dict,
    parse_memory_propose_output,
)
from tests.memory_propose._helpers import valid_cases


class Reader:
    def __init__(self, events: dict[str, EventRecord]) -> None:
        self.events = events

    def get_event(self, event_id: str) -> EventRecord | None:
        return self.events.get(event_id)


def _parsed(case_index: int = 0):
    case = deepcopy(valid_cases()[case_index])
    request = memory_propose_request_from_dict(case["request"])
    result = parse_memory_propose_output(
        json.dumps(case["output"], ensure_ascii=False), request
    )
    return case, request, result


def _events(request) -> dict[str, EventRecord]:
    return {
        event.event_id: EventRecord(
            event_id=event.event_id,
            request_id=f"request-{event.sequence_no}",
            conversation_id=event.conversation_id,
            sequence_no=event.sequence_no,
            occurred_at=event.occurred_at,
            recorded_at=event.occurred_at,
            occurred_timezone=event.timezone,
            actor=event.actor,
            event_type=event.event_type,
            payload={"text": event.text, "status": "complete"},
            source="test",
            causation_event_id=None,
            supersedes_event_id=None,
            schema_version=1,
        )
        for event in request.events
    }


def _materialize(request, result, events):
    return materialize_memory_proposals(
        request,
        result,
        Reader(events),
        clock=lambda: datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
        id_factory=lambda: "fixed-id",
    )


def test_materializes_verified_quotes_and_runtime_owned_fields() -> None:
    _, request, result = _parsed()
    batch = _materialize(request, result, _events(request))

    assert batch.proposal_run_id == request.proposal_run_id
    assert len(batch.representations) == 1
    memory = batch.representations[0]
    quote = result.proposals[0].evidence_quotes[0]
    source_text = next(event.text for event in request.events if event.event_id == quote.event_id)
    evidence = memory.evidence[0]
    assert source_text[evidence.excerpt_start : evidence.excerpt_end] == quote.quote
    assert evidence.excerpt_sha256 == hashlib.sha256(quote.quote.encode("utf-8")).hexdigest()
    assert memory.memory_id == "memory-fixed-id"
    assert memory.status == "proposed"
    assert memory.version == 1
    assert memory.created_at == "2026-08-07T12:00:00Z"
    assert memory.idempotency_key == f"{request.proposal_run_id}:0"


def test_normalizes_resolved_offsets_to_utc() -> None:
    case, _, _ = _parsed()
    temporal = case["output"]["proposals"][0]["temporal"]
    temporal.update(
        {
            "resolution": "resolved",
            "start_at": "2026-08-08T15:00:00+08:00",
            "end_at": "2026-08-08T16:30:00+08:00",
            "timezone": "Asia/Shanghai",
            "precision": "minute",
        }
    )
    request = memory_propose_request_from_dict(case["request"])
    result = parse_memory_propose_output(json.dumps(case["output"], ensure_ascii=False), request)
    memory = _materialize(request, result, _events(request)).representations[0]
    assert memory.temporal.start_at == "2026-08-08T07:00:00Z"
    assert memory.temporal.end_at == "2026-08-08T08:30:00Z"


@pytest.mark.parametrize("field", ["text", "sequence_no", "occurred_at", "occurred_timezone"])
def test_rejects_any_drift_between_input_window_and_ledger(field: str) -> None:
    _, request, result = _parsed()
    events = _events(request)
    event_id = request.events[-1].event_id
    event = events[event_id]
    replacements = {
        "text": event.text + "漂移",
        "sequence_no": event.sequence_no + 1,
        "occurred_at": "2026-08-07T00:00:00Z",
        "occurred_timezone": "UTC",
    }
    values = {name: getattr(event, name) for name in event.__dataclass_fields__}
    if field == "text":
        values["payload"] = {"text": replacements[field], "status": "complete"}
    else:
        values[field] = replacements[field]
    events[event_id] = EventRecord(**values)

    with pytest.raises(MemoryMaterializationError) as caught:
        _materialize(request, result, events)
    assert caught.value.code == "memory_materialize_event_mismatch"


def test_missing_ledger_event_rejects_the_whole_batch() -> None:
    _, request, result = _parsed()
    events = _events(request)
    events.pop(request.events[0].event_id)
    with pytest.raises(MemoryMaterializationError) as caught:
        _materialize(request, result, events)
    assert caught.value.code == "memory_materialize_event_missing"


def test_repeated_quote_requires_start_hint_for_stable_offsets() -> None:
    case, _, _ = _parsed()
    quote = case["output"]["proposals"][0]["evidence_quotes"][0]
    source = next(event for event in case["request"]["events"] if event["event_id"] == quote["event_id"])
    source["text"] = f'{quote["quote"]}，后来又说：{quote["quote"]}'
    quote["start_hint"] = None
    request = memory_propose_request_from_dict(case["request"])
    result = parse_memory_propose_output(json.dumps(case["output"], ensure_ascii=False), request)
    with pytest.raises(MemoryMaterializationError) as caught:
        _materialize(request, result, _events(request))
    assert caught.value.code == "memory_materialize_quote_ambiguous"


def test_source_actor_semantics_are_checked_during_materialization() -> None:
    case, _, _ = _parsed()
    proposal = case["output"]["proposals"][0]
    proposal["kind"] = "player_fact"
    proposal["subject"] = {"type": "player", "entity_id": None, "display_name": None}
    quote = proposal["evidence_quotes"][0]
    character = next(event for event in case["request"]["events"] if event["actor"] == "character")
    quote.update({"event_id": character["event_id"], "quote": character["text"], "start_hint": 0})
    proposal["temporal"] = {
        "relation": "atemporal",
        "resolution": "not_applicable",
        "source_text": None,
        "anchor_event_id": None,
        "start_at": None,
        "end_at": None,
        "timezone": None,
        "precision": "not_applicable",
    }
    request = memory_propose_request_from_dict(case["request"])
    result = parse_memory_propose_output(json.dumps(case["output"], ensure_ascii=False), request)
    with pytest.raises(MemoryMaterializationError) as caught:
        _materialize(request, result, _events(request))
    assert caught.value.code == "memory_materialize_representation_invalid"


def test_empty_result_is_success_without_creating_ids_or_reading_events() -> None:
    _, request, result = _parsed(1)

    class FailingReader:
        def get_event(self, event_id: str) -> EventRecord | None:
            raise AssertionError("empty proposal result must not read evidence")

    batch = materialize_memory_proposals(
        request,
        result,
        FailingReader(),
        id_factory=lambda: (_ for _ in ()).throw(AssertionError("must not allocate ID")),
    )
    assert batch.representations == ()
