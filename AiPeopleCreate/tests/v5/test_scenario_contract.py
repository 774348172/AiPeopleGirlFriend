"""Block A contract tests for V5 runtime-grounded REPLY data.

These tests are deliberately offline. They validate frozen data shapes and
cross-field invariants without invoking a teacher, renderer, or runtime code.
"""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Callable

import pytest
import yaml
from jsonschema import Draft7Validator

from data_gen_v4.runtime_grounded import (
    ScenarioContractError,
    scenario_contract_errors,
    validate_scenario,
    validate_scenario_collection,
)

V5_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = V5_ROOT / "tests" / "v5" / "fixtures" / "runtime_grounded_golden_v1.yaml"
SCHEMA_DIR = V5_ROOT / "data_gen_v4" / "schemas" / "mode_v5"

TASK_TYPES = {
    "reply_accept_authoritative_update",
    "reply_reject_stale_claim",
    "reply_correct_false_premise",
    "reply_resolve_world_memory_conflict",
    "reply_confirm_current_state",
    "reply_subject_attribution",
    "reply_insufficient_information",
    "reply_direct_answer",
}


@pytest.fixture(scope="module")
def golden_doc() -> dict:
    doc = yaml.safe_load(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(doc, dict)
    return doc


@pytest.fixture(scope="module")
def scenarios(golden_doc: dict) -> list[dict]:
    values = golden_doc["scenarios"]
    assert isinstance(values, list)
    return values


def _schema(name: str) -> dict:
    doc = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    Draft7Validator.check_schema(doc)
    return doc


def _schema_errors(name: str, value: dict) -> list:
    return list(Draft7Validator(_schema(name)).iter_errors(value))


def test_fixture_uses_one_shared_full_model_view_anchor(golden_doc: dict) -> None:
    template = golden_doc["model_view_template"]
    expected_keys = {
        "mode",
        "evidence_contract",
        "snapshot",
        "previous_heroine_runtime",
        "selected_memory_frame",
        "current_protagonist_utterance",
        "recent_dialogue",
        "pending_actions",
        "game_feedback",
    }
    assert set(template) == expected_keys
    assert "available_actions" not in template


def test_all_16_golden_scenarios_pass(scenarios: list[dict]) -> None:
    assert len(scenarios) == 16
    validate_scenario_collection(scenarios)
    for scenario in scenarios:
        validate_scenario(scenario)
        assert scenario_contract_errors(scenario) == ()


def test_response_contract_is_task_consistent_when_present(scenarios: list[dict]) -> None:
    unknown = copy.deepcopy(next(row for row in scenarios if row["task_type"] == "reply_insufficient_information"))
    unknown["oracle_view"]["response_contract"] = {
        "unknown_acknowledgement": "required",
        "subject_proposition_binding": "not_applicable",
        "unsupported_expansion_policy": "reject",
    }
    validate_scenario(unknown)
    unknown["oracle_view"]["response_contract"]["unknown_acknowledgement"] = "not_applicable"
    assert any("response_contract_unknown_mismatch" in error for error in scenario_contract_errors(unknown))

    subject = copy.deepcopy(next(row for row in scenarios if row["task_type"] == "reply_subject_attribution"))
    subject["oracle_view"]["response_contract"] = {
        "unknown_acknowledgement": "not_applicable",
        "subject_proposition_binding": "required",
        "unsupported_expansion_policy": "reject",
    }
    validate_scenario(subject)


def test_exactly_two_unique_scenarios_per_task(scenarios: list[dict]) -> None:
    counts = Counter(item["task_type"] for item in scenarios)
    assert set(counts) == TASK_TYPES
    assert counts == Counter({task: 2 for task in TASK_TYPES})
    ids = [item["scenario_id"] for item in scenarios]
    assert len(ids) == len(set(ids))


def _oracle_leak(value: dict) -> None:
    value["model_view"]["oracle_view"] = copy.deepcopy(value["oracle_view"])


def _available_actions(value: dict) -> None:
    value["model_view"]["available_actions"] = ["REPLY"]


def _missing_fact_field(field: str) -> Callable[[dict], None]:
    def mutate(value: dict) -> None:
        del value["oracle_view"]["facts"][0][field]

    return mutate


def _duplicate_fact_id(value: dict) -> None:
    value["oracle_view"]["facts"].append(copy.deepcopy(value["oracle_view"]["facts"][0]))


def _conflicting_current(value: dict) -> None:
    fact = copy.deepcopy(value["oracle_view"]["facts"][0])
    fact["fact_id"] += ".conflict"
    fact["object"] += ".different"
    fact["status"] = "current"
    value["oracle_view"]["facts"].append(fact)


def _same_current_and_stale(value: dict) -> None:
    fact = copy.deepcopy(value["oracle_view"]["facts"][0])
    fact["fact_id"] += ".stale"
    fact["status"] = "stale"
    value["oracle_view"]["facts"].append(fact)


def _direction_mismatch(value: dict) -> None:
    value["oracle_view"]["correction_direction"] = "answer_directly"


def _missing_task_anchor(value: dict) -> None:
    expected = f"task:{value['task_type']}"
    value["split_anchors"].remove(expected)
    value["split_anchors"].append("task:wrong_task")


def _missing_oracle_list(field: str) -> Callable[[dict], None]:
    def mutate(value: dict) -> None:
        del value["oracle_view"][field]

    return mutate


def _missing_utterance(value: dict) -> None:
    del value["model_view"]["current_protagonist_utterance"]


def _unknown_fact_reference(value: dict) -> None:
    value["oracle_view"]["required_assertions"][0]["source_fact_ids"] = ["fact.not.present"]


def _invalid_schema_version(value: dict) -> None:
    value["schema_version"] = "aip.runtime_grounded_reply_scenario.v999"


INVALID_MUTATIONS: list[tuple[str, Callable[[dict], None], str]] = [
    ("oracle_leak", _oracle_leak, "oracle"),
    ("available_actions", _available_actions, "available_actions"),
    ("missing_subject", _missing_fact_field("subject"), "subject"),
    ("missing_source", _missing_fact_field("source"), "source"),
    ("missing_status", _missing_fact_field("status"), "status"),
    ("duplicate_fact_id", _duplicate_fact_id, "duplicate_fact_id"),
    ("conflicting_current", _conflicting_current, "conflicting_current"),
    ("same_current_and_stale", _same_current_and_stale, "same_value_current_and_stale"),
    ("direction_mismatch", _direction_mismatch, "direction_mismatch"),
    ("missing_task_anchor", _missing_task_anchor, "missing_task_split_anchor"),
    ("missing_required_assertions", _missing_oracle_list("required_assertions"), "required_assertions"),
    ("missing_forbidden_assertions", _missing_oracle_list("forbidden_assertions"), "forbidden_assertions"),
    ("missing_current_utterance", _missing_utterance, "current_protagonist_utterance"),
    ("unknown_assertion_fact", _unknown_fact_reference, "unknown_fact_ref"),
    ("invalid_schema_version", _invalid_schema_version, "schema_version"),
]


@pytest.mark.parametrize(
    ("index", "case_name", "mutate", "expected_fragment"),
    [
        (index, case_name, mutate, expected_fragment)
        for index, (case_name, mutate, expected_fragment) in enumerate(INVALID_MUTATIONS)
    ],
    ids=[case[0] for case in INVALID_MUTATIONS],
)
def test_15_single_scenario_invalid_mutations_are_rejected(
    scenarios: list[dict],
    index: int,
    case_name: str,
    mutate: Callable[[dict], None],
    expected_fragment: str,
) -> None:
    value = copy.deepcopy(scenarios[index])
    mutate(value)
    errors = scenario_contract_errors(value)
    assert errors, case_name
    assert any(expected_fragment in error for error in errors), errors
    with pytest.raises(ScenarioContractError):
        validate_scenario(value)


def test_16_duplicate_scenario_id_collection_mutation_is_rejected(scenarios: list[dict]) -> None:
    values = copy.deepcopy(scenarios)
    values[15]["scenario_id"] = values[0]["scenario_id"]
    with pytest.raises(ScenarioContractError, match="duplicate_scenario_id"):
        validate_scenario_collection(values)


def _claim() -> dict:
    return {
        "subject": "protagonist",
        "predicate": "dentist_appointment",
        "object": "星期六上午十点",
        "polarity": "positive",
        "temporal_status": "current",
        "source_fact_ids": ["fact.dentist.sat"],
    }


def test_teacher_target_schema_accepts_minimal_valid_target() -> None:
    value = {"reply": "不是星期五，是星期六上午十点。", "declared_claims": [_claim()]}
    assert _schema_errors("runtime_grounded_teacher_target.schema.json", value) == []


@pytest.mark.parametrize(
    "value",
    [
        {"reply": "", "declared_claims": []},
        {"reply": "可以。"},
        {"reply": "可以。", "declared_claims": [], "scenario": {}},
        {
            "reply": "是星期六上午十点。",
            "declared_claims": [{**_claim(), "source_fact_ids": []}],
        },
    ],
    ids=["empty_reply", "missing_claims", "teacher_generates_scenario", "ungrounded_claim"],
)
def test_teacher_target_schema_rejects_invalid_targets(value: dict) -> None:
    assert _schema_errors("runtime_grounded_teacher_target.schema.json", value)


def _audit(decision: str = "approve") -> dict:
    return {
        "extracted_claims": [_claim()],
        "verdicts": {key: True for key in ("RG5", "RG6", "RG7", "RG8", "RG9", "RG10")},
        "decision": decision,
        "reasons": ["all required claims are grounded"],
    }


def test_audit_schema_accepts_consistent_approve_and_reject() -> None:
    assert _schema_errors("runtime_grounded_audit.schema.json", _audit()) == []
    rejected = _audit("reject")
    rejected["verdicts"]["RG7"] = False
    assert _schema_errors("runtime_grounded_audit.schema.json", rejected) == []


@pytest.mark.parametrize("failed_gate", ["RG5", "RG6", "RG7", "RG8", "RG9", "RG10"])
def test_audit_schema_rejects_approve_when_any_semantic_gate_is_false(failed_gate: str) -> None:
    value = _audit()
    value["verdicts"][failed_gate] = False
    assert _schema_errors("runtime_grounded_audit.schema.json", value)


def test_audit_schema_rejects_missing_gate_and_unknown_fields() -> None:
    missing = _audit()
    del missing["verdicts"]["RG10"]
    assert _schema_errors("runtime_grounded_audit.schema.json", missing)
    extra = _audit()
    extra["judge_score"] = 1.0
    assert _schema_errors("runtime_grounded_audit.schema.json", extra)
    ungrounded = _audit()
    ungrounded["extracted_claims"][0]["source_fact_ids"] = []
    assert _schema_errors("runtime_grounded_audit.schema.json", ungrounded)
