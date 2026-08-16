from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from runtime._memory_contracts import (
    EvidenceSource,
    MemoryContractError,
    memory_contract_to_dict,
    memory_representation_from_dict,
    validate_memory_representation,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / "eval" / "memory_contract" / "fixtures"
SCHEMA = json.loads(
    (ROOT / "runtime" / "schemas" / "memory_representation_v1.schema.json").read_text(
        encoding="utf-8"
    )
)
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sources(case: dict[str, object]) -> dict[str, EvidenceSource]:
    return {
        item["event_id"]: EvidenceSource(**item) for item in case["evidence_sources"]
    }


def _targets(case: dict[str, object]):
    return {
        item["memory_id"]: memory_representation_from_dict(item)
        for item in case["relation_targets"]
    }


def test_all_valid_semantic_boundary_fixtures_pass_schema_and_pure_validation() -> None:
    cases = _load(FIXTURE_ROOT / "valid" / "semantic_boundaries_v1.json")
    assert {case["representation"]["kind"] for case in cases} == {
        "player_fact",
        "preference_boundary",
        "person_relation",
        "shared_experience",
        "relationship_meaning",
        "future_event",
        "unfinished_topic",
        "character_self_claim",
    }
    assert {case["representation"]["epistemic"]["modality"] for case in cases} >= {
        "asserted",
        "uncertain",
        "hypothetical",
        "joking",
        "quoted",
    }
    assert any(
        case["representation"]["epistemic"]["polarity"] == "negated"
        for case in cases
    )
    assert {case["case_id"] for case in cases} >= {"correction", "conflict"}

    for case in cases:
        raw = case["representation"]
        VALIDATOR.validate(raw)
        representation = memory_representation_from_dict(raw)
        validate_memory_representation(
            representation,
            evidence_sources=_sources(case),
            relation_targets=_targets(case),
        )
        assert memory_contract_to_dict(representation) == raw


def test_invalid_evidence_boundary_fixtures_fail_with_frozen_error_codes() -> None:
    cases = _load(FIXTURE_ROOT / "invalid" / "evidence_boundaries_v1.json")
    expected_cases = {
        "cross-conversation",
        "cancelled-output",
        "uncommitted-output",
        "excerpt-mismatch",
        "context-only",
        "character-self-from-player",
        "mutual-with-one-actor",
        "future-date-guessed",
        "empty-evidence",
    }
    assert {case["case_id"] for case in cases} == expected_cases

    for case in cases:
        raw = case["representation"]
        if case["expected_error"] == "memory_schema_invalid":
            with pytest.raises(ValidationError):
                VALIDATOR.validate(raw)
            continue
        VALIDATOR.validate(raw)
        representation = memory_representation_from_dict(raw)
        with pytest.raises(MemoryContractError) as caught:
            validate_memory_representation(
                representation,
                evidence_sources=_sources(case),
                relation_targets=_targets(case),
            )
        assert caught.value.code == case["expected_error"], case["case_id"]


def test_zero_proposal_fixture_is_explicitly_legal() -> None:
    fixture = _load(FIXTURE_ROOT / "valid" / "zero_proposals_v1.json")
    assert fixture == {"proposals": []}
