from __future__ import annotations

import json
import re
from pathlib import Path

from runtime import _memory_contracts as vocabulary


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "eval" / "memory_contract" / "mem01_vocabulary_v1.json"


def _load_contract() -> dict[str, object]:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_runtime_enums_match_frozen_vocabulary() -> None:
    contract = _load_contract()
    enums = contract["enums"]
    expected = {
        "memory_kinds": vocabulary.MEMORY_KINDS,
        "subject_types": vocabulary.SUBJECT_TYPES,
        "epistemic_polarities": vocabulary.EPISTEMIC_POLARITIES,
        "epistemic_modalities": vocabulary.EPISTEMIC_MODALITIES,
        "epistemic_groundings": vocabulary.EPISTEMIC_GROUNDINGS,
        "temporal_relations": vocabulary.TEMPORAL_RELATIONS,
        "temporal_resolutions": vocabulary.TEMPORAL_RESOLUTIONS,
        "temporal_precisions": vocabulary.TEMPORAL_PRECISIONS,
        "evidence_roles": vocabulary.EVIDENCE_ROLES,
        "memory_relation_types": vocabulary.MEMORY_RELATION_TYPES,
        "memory_statuses": vocabulary.MEMORY_STATUSES,
        "memory_indexable_statuses": vocabulary.MEMORY_INDEXABLE_STATUSES,
        "memory_terminal_statuses": vocabulary.MEMORY_TERMINAL_STATUSES,
    }
    assert contract["schema_version"] == vocabulary.MEMORY_SCHEMA_VERSION
    assert {name: tuple(values) for name, values in enums.items()} == expected


def test_field_sets_and_owners_match_runtime_contract() -> None:
    contract = _load_contract()
    fields = contract["fields"]
    assert tuple(fields["draft"]) == vocabulary.DRAFT_FIELDS
    assert tuple(fields["representation"]) == vocabulary.REPRESENTATION_FIELDS
    assert tuple(fields["subject"]) == vocabulary.SUBJECT_FIELDS
    assert tuple(fields["epistemic"]) == vocabulary.EPISTEMIC_FIELDS
    assert tuple(fields["temporal"]) == vocabulary.TEMPORAL_FIELDS
    assert tuple(fields["evidence_quote"]) == vocabulary.EVIDENCE_QUOTE_FIELDS
    assert tuple(fields["evidence"]) == vocabulary.EVIDENCE_FIELDS
    assert tuple(fields["relations"]) == vocabulary.RELATION_FIELDS
    assert contract["field_owners"] == dict(vocabulary.FIELD_OWNERS)
    assert set(vocabulary.FIELD_OWNERS) == set(vocabulary.REPRESENTATION_FIELDS)


def test_model_draft_has_no_runtime_authority() -> None:
    contract = _load_contract()
    privileged = {
        "schema_version",
        "memory_id",
        "version",
        "conversation_id",
        "proposal_run_id",
        "proposal_ordinal",
        "evidence",
        "status",
        "created_at",
        "idempotency_key",
    }
    assert set(contract["fields"]["draft"]).isdisjoint(privileged)
    assert all(contract["field_owners"][name] == "runtime" for name in privileged)
    assert contract["trust_boundaries"] == {
        "model_may_activate_memory": False,
        "model_may_assign_memory_id": False,
        "model_may_assign_created_at": False,
        "model_may_assign_evidence_offsets": False,
        "model_may_assign_evidence_hash": False,
        "future_event_is_reminder": False,
        "original_events_remain_truth_source": True,
        "empty_proposal_batch_is_valid": True,
    }


def test_limits_are_explicit_and_bounded() -> None:
    contract = _load_contract()
    assert contract["limits"] == dict(vocabulary.FIELD_LIMITS)
    limits = vocabulary.FIELD_LIMITS
    assert 0 < limits["statement_chars_min"] <= limits["statement_chars_max"]
    assert 0 < limits["semantic_reason_chars_min"] <= limits["semantic_reason_chars_max"]
    assert 0 < limits["evidence_count_min"] <= limits["evidence_count_max"] <= 8
    assert limits["confidence_min"] == 0.0
    assert limits["confidence_max"] == 1.0
    assert limits["version_min"] == 1
    assert limits["proposal_ordinal_min"] == 0


def test_lifecycle_matches_frozen_vocabulary() -> None:
    contract = _load_contract()
    lifecycle = contract["lifecycle"]
    assert lifecycle["initial_status"] == vocabulary.INITIAL_MEMORY_STATUS
    assert {
        state: frozenset(targets)
        for state, targets in lifecycle["transitions"].items()
    } == dict(vocabulary.MEMORY_STATUS_TRANSITIONS)
    assert set(lifecycle["transitions"]) == set(vocabulary.MEMORY_STATUSES)
    assert all(
        not vocabulary.MEMORY_STATUS_TRANSITIONS[state]
        for state in vocabulary.MEMORY_TERMINAL_STATUSES
    )


def test_enum_values_are_unique_closed_snake_case_terms() -> None:
    enums = _load_contract()["enums"]
    for values in enums.values():
        assert len(values) == len(set(values))
        assert all(re.fullmatch(r"[a-z][a-z0-9_]*", value) for value in values)


def test_future_events_are_not_plan_or_notification_vocabulary() -> None:
    contract = _load_contract()
    assert "future_event" in contract["enums"]["memory_kinds"]
    forbidden_fields = {"due_at", "notification", "reminder", "timer", "send_at"}
    all_fields = {
        field
        for field_group in contract["fields"].values()
        for field in field_group
    }
    assert all_fields.isdisjoint(forbidden_fields)

