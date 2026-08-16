from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from runtime._memory_contracts import (
    EvidenceQuote,
    MemoryContractError,
    MemoryEpistemic,
    MemoryProposalDraft,
    MemoryRepresentation,
    MemorySubject,
    MemoryTemporal,
    canonical_memory_json,
    memory_contract_to_dict,
    memory_proposal_draft_from_dict,
    memory_proposal_draft_from_json,
    memory_representation_from_dict,
    memory_representation_from_json,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "runtime" / "schemas"


def _draft_dict() -> dict[str, object]:
    return {
        "kind": "future_event",
        "statement": "玩家表示下周可能去上海出差，但尚未确定",
        "subject": {
            "type": "player",
            "entity_id": None,
            "display_name": None,
        },
        "epistemic": {
            "polarity": "affirmed",
            "modality": "uncertain",
            "grounding": "speaker_report",
        },
        "temporal": {
            "relation": "future",
            "resolution": "ambiguous",
            "source_text": "下周",
            "anchor_event_id": "event-user-123",
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "precision": "unknown",
        },
        "evidence_quotes": [
            {
                "event_id": "event-user-123",
                "role": "support",
                "quote": "我下周可能去上海出差，还没完全定。",
                "start_hint": None,
            }
        ],
        "semantic_reason": "数日后继续聊天时可能自然关心出差是否确定",
        "confidence": 0.96,
        "relation_suggestions": {
            "semantic_slot": "player.future.travel.shanghai",
            "supersedes": [],
            "contradicts": [],
            "refines": [],
        },
    }


def _representation_dict() -> dict[str, object]:
    value = _draft_dict()
    value["schema_version"] = 1
    value["memory_id"] = "memory-123"
    value["version"] = 1
    value["conversation_id"] = "conversation-123"
    value["proposal_run_id"] = "proposal-run-123"
    value["proposal_ordinal"] = 0
    value["evidence"] = [
        {
            "event_id": "event-user-123",
            "role": "support",
            "excerpt_start": 0,
            "excerpt_end": 20,
            "excerpt": "我下周可能去上海出差，还没完全定。",
            "excerpt_sha256": "a" * 64,
        }
    ]
    value["relations"] = value.pop("relation_suggestions")
    value.pop("evidence_quotes")
    value["status"] = "proposed"
    value["created_at"] = "2026-08-07T12:00:00Z"
    value["idempotency_key"] = "proposal-run-123:0"
    return value


def _validate_schema(name: str, value: dict[str, object]) -> None:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    Draft202012Validator(
        schema, format_checker=FormatChecker()
    ).validate(value)


def test_draft_round_trips_between_dict_and_immutable_contract() -> None:
    source = _draft_dict()
    draft = memory_proposal_draft_from_dict(source)
    assert isinstance(draft, MemoryProposalDraft)
    assert isinstance(draft.subject, MemorySubject)
    assert isinstance(draft.epistemic, MemoryEpistemic)
    assert isinstance(draft.temporal, MemoryTemporal)
    assert isinstance(draft.evidence_quotes, tuple)
    assert memory_contract_to_dict(draft) == source
    _validate_schema("memory_proposal_draft_v1.schema.json", memory_contract_to_dict(draft))


def test_representation_round_trips_and_remains_schema_valid() -> None:
    source = _representation_dict()
    representation = memory_representation_from_dict(source)
    assert isinstance(representation, MemoryRepresentation)
    assert isinstance(representation.evidence, tuple)
    assert memory_contract_to_dict(representation) == source
    _validate_schema(
        "memory_representation_v1.schema.json",
        memory_contract_to_dict(representation),
    )


def test_contracts_are_frozen_slotted_and_nested_arrays_are_tuples() -> None:
    draft = memory_proposal_draft_from_dict(_draft_dict())
    assert not hasattr(draft, "__dict__")
    assert not hasattr(draft.subject, "__dict__")
    with pytest.raises(FrozenInstanceError):
        draft.kind = "player_fact"
    with pytest.raises(AttributeError):
        draft.evidence_quotes.append(draft.evidence_quotes[0])
    with pytest.raises(TypeError, match="evidence_quotes must be a tuple"):
        replace(draft, evidence_quotes=list(draft.evidence_quotes))


def test_json_parsers_reject_duplicate_fields_and_nonfinite_numbers() -> None:
    with pytest.raises(MemoryContractError, match="duplicate JSON field"):
        memory_proposal_draft_from_json('{"kind":"future_event","kind":"player_fact"}')

    source = _draft_dict()
    source["confidence"] = float("nan")
    encoded = json.dumps(source, ensure_ascii=False)
    with pytest.raises(MemoryContractError, match="non-finite JSON number"):
        memory_proposal_draft_from_json(encoded)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("confidence",), "0.96"),
        (("confidence",), True),
        (("evidence_quotes", 0, "start_hint"), 0.0),
        (("subject", "entity_id"), 123),
        (("relation_suggestions", "supersedes"), "memory-1"),
    ],
)
def test_parser_rejects_implicit_type_coercion(
    path: tuple[object, ...], value: object
) -> None:
    source = _draft_dict()
    target = source
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises((TypeError, MemoryContractError)):
        memory_proposal_draft_from_dict(source)


def test_parser_rejects_missing_unknown_and_non_string_keys() -> None:
    missing = _draft_dict()
    missing.pop("kind")
    with pytest.raises(MemoryContractError, match="missing=kind"):
        memory_proposal_draft_from_dict(missing)

    unknown = _draft_dict()
    unknown["status"] = "active"
    with pytest.raises(MemoryContractError, match="unknown=status"):
        memory_proposal_draft_from_dict(unknown)

    nested_unknown = _draft_dict()
    nested_unknown["subject"]["unknown"] = True
    with pytest.raises(MemoryContractError, match="unknown=unknown"):
        memory_proposal_draft_from_dict(nested_unknown)

    invalid_key = _draft_dict()
    invalid_key[1] = "not-a-string-key"
    with pytest.raises(MemoryContractError, match="keys must be strings") as caught:
        memory_proposal_draft_from_dict(invalid_key)
    assert caught.value.code == "memory_schema_invalid"


def test_canonical_json_is_unicode_stable_and_input_order_independent() -> None:
    source = _draft_dict()
    reordered = dict(reversed(tuple(source.items())))
    first = memory_proposal_draft_from_dict(source)
    second = memory_proposal_draft_from_dict(reordered)
    first_bytes = canonical_memory_json(first)
    assert first_bytes == canonical_memory_json(second)
    assert "上海".encode("utf-8") in first_bytes
    assert b" " not in first_bytes
    assert memory_proposal_draft_from_json(first_bytes) == first


def test_materialized_times_must_be_utc_but_draft_may_hold_offset_input() -> None:
    draft_source = _draft_dict()
    draft_source["temporal"]["resolution"] = "resolved"
    draft_source["temporal"]["start_at"] = "2026-08-08T15:00:00+08:00"
    draft_source["temporal"]["timezone"] = "Asia/Shanghai"
    memory_proposal_draft_from_dict(draft_source)

    representation_source = _representation_dict()
    representation_source["temporal"]["resolution"] = "resolved"
    representation_source["temporal"]["start_at"] = "2026-08-08T15:00:00+08:00"
    representation_source["temporal"]["timezone"] = "Asia/Shanghai"
    with pytest.raises(MemoryContractError, match="UTC Z"):
        memory_representation_from_dict(representation_source)

    representation_source = _representation_dict()
    representation_source["created_at"] = "2026-08-07T20:00:00+08:00"
    with pytest.raises(MemoryContractError, match="UTC Z"):
        memory_representation_from_dict(representation_source)


def test_representation_rejects_bool_for_integer_fields() -> None:
    for field in ("schema_version", "version", "proposal_ordinal"):
        source = _representation_dict()
        source[field] = True
        with pytest.raises(MemoryContractError, match="must be an integer") as caught:
            memory_representation_from_dict(source)
        assert caught.value.code == "memory_schema_invalid"


def test_json_and_dict_entrypoints_produce_equal_values() -> None:
    draft_source = _draft_dict()
    representation_source = _representation_dict()
    assert memory_proposal_draft_from_json(
        json.dumps(draft_source, ensure_ascii=False)
    ) == memory_proposal_draft_from_dict(draft_source)
    assert memory_representation_from_json(
        json.dumps(representation_source, ensure_ascii=False)
    ) == memory_representation_from_dict(representation_source)


def test_serializer_rejects_unrelated_objects() -> None:
    with pytest.raises(TypeError, match="MemoryProposalDraft or MemoryRepresentation"):
        memory_contract_to_dict(EvidenceQuote("event-1", "support", "x", None))
