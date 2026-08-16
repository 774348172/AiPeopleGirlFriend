from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import FreezeVerificationError, verify_file
from eval.chat01.leakage_guard import sha256_file
from eval.chat02f.freeze import (
    CONTRACT_PATH as CONTRACT_V1_PATH,
    PRIOR_HUMAN_ROOTS,
    REPORT_PATH,
    ROOT,
    _artifact,
    _case_inventory,
    _load_json,
    _tree_commitment,
    _write_json,
    verify_frozen as verify_v1,
)


DISPOSITION_PATH = ROOT / "eval" / "chat02f" / "leakage_disposition_v1.json"
CONTRACT_V2_PATH = ROOT / "eval" / "chat02f" / "comparison_contract_v2.json"
DISPOSITION_SCHEMA = ROOT / "eval" / "chat02f" / "schema" / "leakage_disposition.schema.json"
CONTRACT_V2_SCHEMA = ROOT / "eval" / "chat02f" / "schema" / "comparison_contract_v2.schema.json"
DIAGNOSTIC_CASE_ID = "frozen.identity.name"


def _validate(value: dict[str, Any], schema_path: Path) -> None:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _canonical_sha256(value: dict[str, Any], hash_field: str) -> str:
    canonical = deepcopy(value)
    canonical["freeze"][hash_field] = None
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _assert_no_chat02f_candidate_outputs() -> None:
    report_root = ROOT / "eval" / "chat02f" / "reports"
    if report_root.exists() and any(path.is_file() for path in report_root.rglob("*")):
        raise FreezeVerificationError("CHAT-02F candidate outputs already exist; disposition timing cannot be pre-output")


def _finding() -> dict[str, Any]:
    report = _load_json(REPORT_PATH)
    findings = report["findings"]
    if len(findings) != 1:
        raise FreezeVerificationError("expected exactly one CHAT-02F leakage finding")
    finding = findings[0]
    if finding["finding_id"] != "leak.46a6d068aa20be544a0e27b2" or finding["kind"] != "exact":
        raise FreezeVerificationError("unexpected CHAT-02F leakage finding")
    return finding


def build_disposition() -> dict[str, Any]:
    verify_v1()
    _assert_no_chat02f_candidate_outputs()
    finding = _finding()
    disposition = {
        "disposition_id": "chat02f-leakage-disposition-v1",
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "authority": "explicit_user_decision",
        "decision_timing": "before_chat02f_candidate_outputs",
        "leakage_evidence": _artifact(REPORT_PATH),
        "finding": {
            "finding_id": finding["finding_id"],
            "kind": finding["kind"],
            "case_id": DIAGNOSTIC_CASE_ID,
            "evaluation_text_sha256": finding["evaluation"]["text_sha256"],
            "training_path": finding["training"]["path"],
            "training_line": finding["training"]["line"],
        },
        "policy": {
            "suite_action": "retain_suite_v3_unchanged",
            "execution_action": "execute_with_all_240_frozen_single_cases",
            "reporting_action": "report_case_as_contaminated_diagnostic_only",
            "primary_weight_denominator_action": "exclude_case_from_primary_aggregate_and_model_delta",
            "replacement_action": "none",
        },
        "denominators": {
            "executed_frozen_single": 240,
            "primary_frozen_single": 239,
            "diagnostic_only_frozen_single": 1,
        },
        "scope": "chat02f_v2500_vs_base_weight_comparison_only",
        "rationale": "The candidate corpus contains the exact frozen identity question, so this case cannot support a clean causal claim about weight quality. Keeping and executing it preserves suite v3 and diagnostic visibility; excluding only its primary aggregate contribution removes the known direct-exposure advantage without replacing or editing a frozen case.",
        "freeze": {
            "immutable": True,
            "hash_algorithm": "sha256",
            "hash_mode": "canonical_json_with_null_document_sha256",
            "document_sha256": None,
        },
    }
    disposition["freeze"]["document_sha256"] = _canonical_sha256(disposition, "document_sha256")
    _validate(disposition, DISPOSITION_SCHEMA)
    return disposition


def build_contract_v2() -> dict[str, Any]:
    v1 = verify_v1()
    disposition = _load_json(DISPOSITION_PATH)
    _validate(disposition, DISPOSITION_SCHEMA)
    contract = {
        "contract_id": "chat02f-comparison-contract-v2",
        "schema_version": 2,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "frozen_ready_with_diagnostic_exclusion",
        "supersedes": _artifact(CONTRACT_V1_PATH),
        "only_active_variable": v1["only_active_variable"],
        "models": v1["models"],
        "suite": v1["suite"],
        "prompt_renderer": v1["prompt_renderer"],
        "case_inventory": v1["case_inventory"],
        "analysis_population": {
            "all_suite_cases_executed": True,
            "primary_denominators": {
                "frozen_single": 239,
                "frozen_multiturn": 30,
                "human_pair": 60,
                "human_long_session": 8,
            },
            "diagnostic_only_case_ids": [DIAGNOSTIC_CASE_ID],
            "diagnostic_denominators": {"frozen_single": 1},
            "aggregation_rule": "execute_and_report_verbatim_but_exclude_from_primary_pass_rate_score_delta_and_weight_benefit_claims",
        },
        "run_order": v1["run_order"],
        "leakage_evidence": v1["leakage_evidence"],
        "leakage_disposition": _artifact(DISPOSITION_PATH),
        "excluded_prior_human_assets": v1["excluded_prior_human_assets"],
        "gates": {
            "suite_verified": True,
            "model_assets_verified": True,
            "prompt_frozen": True,
            "leakage_status": "mitigated_by_diagnostic_exclusion",
            "disposition_verified": True,
            "can_start_chat02f_c": True,
            "blockers": [],
            "limitations": [
                "The local candidate corpus hashes are not proven to be the exact training-machine inputs.",
                "frozen.identity.name must never contribute to primary aggregate, model-delta, or weight-benefit claims.",
            ],
        },
        "freeze": {
            "immutable": True,
            "hash_algorithm": "sha256",
            "hash_mode": "canonical_json_with_null_contract_sha256",
            "contract_sha256": None,
        },
    }
    contract["freeze"]["contract_sha256"] = _canonical_sha256(contract, "contract_sha256")
    _validate(contract, CONTRACT_V2_SCHEMA)
    return contract


def freeze_v2() -> dict[str, Any]:
    if DISPOSITION_PATH.exists() or CONTRACT_V2_PATH.exists():
        raise FreezeVerificationError("CHAT-02F disposition or comparison contract v2 already exists and is immutable")
    disposition = build_disposition()
    _write_json(DISPOSITION_PATH, disposition)
    contract = build_contract_v2()
    _write_json(CONTRACT_V2_PATH, contract)
    return verify_v2()


def verify_v2() -> dict[str, Any]:
    v1 = verify_v1()
    disposition = _load_json(DISPOSITION_PATH)
    contract = _load_json(CONTRACT_V2_PATH)
    _validate(disposition, DISPOSITION_SCHEMA)
    _validate(contract, CONTRACT_V2_SCHEMA)
    if disposition["freeze"]["document_sha256"] != _canonical_sha256(disposition, "document_sha256"):
        raise FreezeVerificationError("CHAT-02F leakage disposition self SHA256 changed")
    if contract["freeze"]["contract_sha256"] != _canonical_sha256(contract, "contract_sha256"):
        raise FreezeVerificationError("CHAT-02F comparison contract v2 self SHA256 changed")
    for artifact in (contract["supersedes"], contract["suite"]["manifest"], contract["prompt_renderer"], contract["leakage_evidence"], contract["leakage_disposition"]):
        verify_file(ROOT, artifact)
    for model in contract["models"]:
        verify_file(ROOT, model["manifest"])
        verify_file(ROOT, model["gguf"])
    if contract["models"] != v1["models"] or contract["suite"] != v1["suite"] or contract["prompt_renderer"] != v1["prompt_renderer"]:
        raise FreezeVerificationError("v2 changed a v1 model, suite, or prompt binding")
    if contract["case_inventory"] != _case_inventory() or contract["case_inventory"]["frozen_single"] != 240:
        raise FreezeVerificationError("suite execution inventory changed")
    if contract["excluded_prior_human_assets"]["roots"] != [_tree_commitment(path) for path in PRIOR_HUMAN_ROOTS]:
        raise FreezeVerificationError("excluded CHAT-02E human asset commitment changed")
    finding = _finding()
    if disposition["finding"]["finding_id"] != finding["finding_id"]:
        raise FreezeVerificationError("disposition no longer binds the leakage finding")
    primary = contract["analysis_population"]["primary_denominators"]["frozen_single"]
    diagnostic = contract["analysis_population"]["diagnostic_denominators"]["frozen_single"]
    if primary + diagnostic != contract["case_inventory"]["frozen_single"]:
        raise FreezeVerificationError("primary and diagnostic denominators do not cover all executed single-turn cases")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify CHAT-02F comparison contract v2 after leakage disposition.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        contract = freeze_v2() if args.freeze else verify_v2()
    except (FreezeVerificationError, OSError, ValueError) as exc:
        print(f"CHAT-02F contract v2 verification failed: {exc}")
        return 2
    print(json.dumps({
        "contract_id": contract["contract_id"],
        "status": contract["status"],
        "executed_frozen_single": contract["case_inventory"]["frozen_single"],
        "primary_frozen_single": contract["analysis_population"]["primary_denominators"]["frozen_single"],
        "diagnostic_only_case_ids": contract["analysis_population"]["diagnostic_only_case_ids"],
        "can_start_chat02f_c": contract["gates"]["can_start_chat02f_c"],
        "contract_sha256": contract["freeze"]["contract_sha256"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
