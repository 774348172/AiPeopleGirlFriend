from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from runtime._memory_contracts import (
    MemoryContractError,
    MemoryRelations,
    validate_memory_idempotency,
    validate_memory_proposal_draft,
    validate_memory_representation,
    validate_memory_transition,
    memory_proposal_draft_from_dict,
    memory_representation_from_dict,
)

from ._helpers import draft_dict, representation_dict, source


def _assert_error(code: str, call) -> MemoryContractError:
    with pytest.raises(MemoryContractError) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.path
    return caught.value


@pytest.mark.parametrize(
    ("kind", "subject_type"),
    [
        ("player_fact", "player"),
        ("preference_boundary", "relationship"),
        ("person_relation", "third_party"),
        ("shared_experience", "both"),
        ("relationship_meaning", "relationship"),
        ("future_event", "character"),
        ("unfinished_topic", "third_party"),
        ("character_self_claim", "character"),
    ],
)
def test_kind_subject_valid_combinations(kind: str, subject_type: str) -> None:
    value = draft_dict()
    value["kind"] = kind
    value["subject"]["type"] = subject_type
    if subject_type == "third_party":
        value["subject"].update(entity_id="person-1", display_name="小林")
    if kind != "future_event":
        value["temporal"] = {
            "relation": "atemporal",
            "resolution": "not_applicable",
            "source_text": None,
            "anchor_event_id": None,
            "start_at": None,
            "end_at": None,
            "timezone": None,
            "precision": "not_applicable",
        }
    validate_memory_proposal_draft(memory_proposal_draft_from_dict(value))


def test_subject_kind_and_third_party_rules_reject_invalid_combinations() -> None:
    value = draft_dict()
    value["kind"] = "player_fact"
    value["subject"]["type"] = "character"
    _assert_error(
        "memory_cross_field_invalid",
        lambda: validate_memory_proposal_draft(memory_proposal_draft_from_dict(value)),
    )

    value = draft_dict()
    value["subject"]["type"] = "third_party"
    _assert_error(
        "memory_cross_field_invalid",
        lambda: validate_memory_proposal_draft(memory_proposal_draft_from_dict(value)),
    )

    value = draft_dict()
    value["subject"]["entity_id"] = "forbidden"
    _assert_error(
        "memory_cross_field_invalid",
        lambda: validate_memory_proposal_draft(memory_proposal_draft_from_dict(value)),
    )


@pytest.mark.parametrize("resolution", ["relative", "ambiguous"])
def test_unresolved_time_preserves_source_and_anchor(resolution: str) -> None:
    value = draft_dict()
    value["temporal"]["resolution"] = resolution
    validate_memory_proposal_draft(memory_proposal_draft_from_dict(value))


def test_resolved_and_not_applicable_time_combinations() -> None:
    value = draft_dict()
    value["temporal"] = {
        "relation": "future",
        "resolution": "resolved",
        "source_text": "明天下午三点",
        "anchor_event_id": "event-user-1",
        "start_at": "2026-08-08T07:00:00Z",
        "end_at": "2026-08-08T08:00:00Z",
        "timezone": "Asia/Shanghai",
        "precision": "hour",
    }
    validate_memory_proposal_draft(memory_proposal_draft_from_dict(value))

    value["temporal"]["end_at"] = "2026-08-08T06:00:00Z"
    _assert_error(
        "memory_cross_field_invalid",
        lambda: validate_memory_proposal_draft(memory_proposal_draft_from_dict(value)),
    )

    value = draft_dict()
    value["kind"] = "player_fact"
    value["temporal"] = {
        "relation": "atemporal",
        "resolution": "not_applicable",
        "source_text": None,
        "anchor_event_id": None,
        "start_at": None,
        "end_at": None,
        "timezone": None,
        "precision": "not_applicable",
    }
    validate_memory_proposal_draft(memory_proposal_draft_from_dict(value))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda t: t.update(source_text=None),
        lambda t: t.update(start_at="2026-08-08T07:00:00Z"),
        lambda t: t.update(timezone="Mars/Olympus"),
        lambda t: t.update(relation="present"),
    ],
)
def test_temporal_invalid_combinations_have_cross_field_code(mutation) -> None:
    value = draft_dict()
    mutation(value["temporal"])
    _assert_error(
        "memory_cross_field_invalid",
        lambda: validate_memory_proposal_draft(memory_proposal_draft_from_dict(value)),
    )


def test_evidence_excerpt_hash_offsets_and_source_are_verified() -> None:
    raw = representation_dict()
    representation = memory_representation_from_dict(raw)
    validate_memory_representation(
        representation, evidence_sources={"event-user-1": source()}
    )

    for field, changed in (
        ("excerpt_end", raw["evidence"][0]["excerpt_end"] - 1),
        ("excerpt_sha256", "0" * 64),
        ("excerpt_start", 1),
    ):
        invalid = deepcopy(raw)
        invalid["evidence"][0][field] = changed
        _assert_error(
            "memory_evidence_excerpt_mismatch",
            lambda invalid=invalid: validate_memory_representation(
                memory_representation_from_dict(invalid),
                evidence_sources={"event-user-1": source()},
            ),
        )


@pytest.mark.parametrize(
    ("source_change", "expected_code"),
    [
        ({"committed": False}, "memory_evidence_uncommitted"),
        ({"event_type": "generation_cancelled"}, "memory_evidence_uncommitted"),
        ({"conversation_id": "conversation-other"}, "memory_evidence_cross_conversation"),
    ],
)
def test_evidence_source_boundaries(source_change, expected_code: str) -> None:
    representation = memory_representation_from_dict(representation_dict())
    _assert_error(
        expected_code,
        lambda: validate_memory_representation(
            representation,
            evidence_sources={"event-user-1": source(**source_change)},
        ),
    )


def test_grounding_and_character_self_claim_require_correct_actors() -> None:
    raw = representation_dict()
    raw["epistemic"]["grounding"] = "mutually_confirmed"
    representation = memory_representation_from_dict(raw)
    _assert_error(
        "memory_evidence_missing",
        lambda: validate_memory_representation(
            representation, evidence_sources={"event-user-1": source()}
        ),
    )

    raw = representation_dict()
    raw["kind"] = "character_self_claim"
    raw["subject"]["type"] = "character"
    representation = memory_representation_from_dict(raw)
    _assert_error(
        "memory_evidence_missing",
        lambda: validate_memory_representation(
            representation, evidence_sources={"event-user-1": source()}
        ),
    )


def test_relations_reject_self_overlap_missing_and_cross_conversation_targets() -> None:
    raw = representation_dict()
    raw["relations"]["supersedes"] = ["memory-1"]
    _assert_error(
        "memory_cross_field_invalid",
        lambda: validate_memory_representation(memory_representation_from_dict(raw)),
    )

    raw["relations"]["supersedes"] = ["memory-old"]
    raw["relations"]["contradicts"] = ["memory-old"]
    _assert_error(
        "memory_cross_field_invalid",
        lambda: validate_memory_representation(memory_representation_from_dict(raw)),
    )

    raw["relations"]["contradicts"] = []
    representation = memory_representation_from_dict(raw)
    _assert_error(
        "memory_relation_missing_target",
        lambda: validate_memory_representation(representation, relation_targets={}),
    )

    target_raw = representation_dict(conversation_id="conversation-other")
    target_raw["memory_id"] = "memory-old"
    target = memory_representation_from_dict(target_raw)
    _assert_error(
        "memory_relation_cross_conversation",
        lambda: validate_memory_representation(
            representation, relation_targets={"memory-old": target}
        ),
    )


def test_lifecycle_and_idempotency_contracts() -> None:
    for from_status, to_status in (
        (None, "proposed"),
        ("proposed", "active"),
        ("active", "disputed"),
        ("disputed", "superseded"),
    ):
        validate_memory_transition(from_status, to_status)
    _assert_error(
        "memory_transition_invalid",
        lambda: validate_memory_transition("rejected", "active"),
    )

    existing = memory_representation_from_dict(representation_dict())
    validate_memory_idempotency(existing, existing)
    conflict = replace(existing, statement="不同内容")
    _assert_error(
        "memory_idempotency_conflict",
        lambda: validate_memory_idempotency(existing, conflict),
    )


def test_unknown_field_and_enum_codes_are_stable() -> None:
    value = draft_dict()
    value["runtime_status"] = "active"
    _assert_error(
        "memory_unknown_field", lambda: memory_proposal_draft_from_dict(value)
    )

    value = draft_dict()
    value["kind"] = "reminder"
    _assert_error(
        "memory_invalid_enum", lambda: memory_proposal_draft_from_dict(value)
    )
