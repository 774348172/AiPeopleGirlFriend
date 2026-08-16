from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import FreezeVerificationError
from eval.chat02f.freeze import CONTRACT_PATH as CONTRACT_V1_PATH
from eval.chat02f.freeze_v2 import (
    CONTRACT_V2_PATH,
    CONTRACT_V2_SCHEMA,
    DISPOSITION_PATH,
    DISPOSITION_SCHEMA,
    freeze_v2,
    verify_v2,
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_disposition_and_contract_v2_validate_and_verify_live_assets() -> None:
    disposition = _load(DISPOSITION_PATH)
    contract = verify_v2()
    for value, schema_path in ((disposition, DISPOSITION_SCHEMA), (contract, CONTRACT_V2_SCHEMA)):
        schema = _load(schema_path)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_explicit_pre_output_decision_preserves_suite_and_case() -> None:
    disposition = _load(DISPOSITION_PATH)
    assert disposition["authority"] == "explicit_user_decision"
    assert disposition["decision_timing"] == "before_chat02f_candidate_outputs"
    assert disposition["finding"]["case_id"] == "frozen.identity.name"
    assert disposition["policy"] == {
        "suite_action": "retain_suite_v3_unchanged",
        "execution_action": "execute_with_all_240_frozen_single_cases",
        "reporting_action": "report_case_as_contaminated_diagnostic_only",
        "primary_weight_denominator_action": "exclude_case_from_primary_aggregate_and_model_delta",
        "replacement_action": "none",
    }


def test_all_240_execute_but_primary_weight_denominator_is_239() -> None:
    contract = _load(CONTRACT_V2_PATH)
    population = contract["analysis_population"]
    assert contract["case_inventory"]["frozen_single"] == 240
    assert population["all_suite_cases_executed"] is True
    assert population["primary_denominators"]["frozen_single"] == 239
    assert population["diagnostic_only_case_ids"] == ["frozen.identity.name"]
    assert population["diagnostic_denominators"] == {"frozen_single": 1}
    assert "exclude_from_primary" in population["aggregation_rule"]


def test_v2_changes_analysis_population_not_model_suite_or_prompt() -> None:
    v1 = _load(CONTRACT_V1_PATH)
    v2 = _load(CONTRACT_V2_PATH)
    assert v2["models"] == v1["models"]
    assert v2["suite"] == v1["suite"]
    assert v2["prompt_renderer"] == v1["prompt_renderer"]
    assert v2["run_order"] == v1["run_order"]
    assert v2["excluded_prior_human_assets"] == v1["excluded_prior_human_assets"]
    assert v2["supersedes"]["path"] == "eval/chat02f/comparison_contract_v1.json"


def test_v2_opens_chat02f_c_with_recorded_limitations() -> None:
    contract = _load(CONTRACT_V2_PATH)
    assert contract["status"] == "frozen_ready_with_diagnostic_exclusion"
    assert contract["gates"]["leakage_status"] == "mitigated_by_diagnostic_exclusion"
    assert contract["gates"]["disposition_verified"] is True
    assert contract["gates"]["can_start_chat02f_c"] is True
    assert contract["gates"]["blockers"] == []
    assert contract["gates"]["limitations"]


def test_v1_remains_frozen_blocked_as_audit_history() -> None:
    v1 = _load(CONTRACT_V1_PATH)
    assert v1["status"] == "frozen_blocked"
    assert v1["gates"]["can_start_chat02f_c"] is False
    assert v1["freeze"]["contract_sha256"] == "c06423813cc70b7bff18d1b602fe1bd7b6b8760a52649116a317a0f1aba68808"


def test_frozen_disposition_and_v2_refuse_overwrite() -> None:
    with pytest.raises(FreezeVerificationError, match="already exists"):
        freeze_v2()
