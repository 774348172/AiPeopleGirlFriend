from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat01.freeze import FreezeVerificationError, verify_file, verify_manifest
from eval.chat01.leakage_guard import scan_leakage, sha256_file


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "eval" / "chat02f" / "leakage_evidence_v1.json"
CONTRACT_PATH = ROOT / "eval" / "chat02f" / "comparison_contract_v1.json"
REPORT_SCHEMA = ROOT / "eval" / "chat02f" / "schema" / "leakage_evidence.schema.json"
CONTRACT_SCHEMA = ROOT / "eval" / "chat02f" / "schema" / "comparison_contract.schema.json"
SUITE_PATH = ROOT / "eval" / "chat01" / "suites" / "chat01_suite_manifest_v3.json"
PROMPT_PATH = ROOT / "runtime" / "_prompt.py"
BASE_MANIFEST_PATH = ROOT / "eval" / "chat02" / "models" / "qwen3-4b-base-q4_k_m.json"
CANDIDATE_MANIFEST_PATH = ROOT / "eval" / "chat02f" / "models" / "qinweixi-v2500-final-q4_k_m.json"
TRAINING_PATTERNS = (
    "training_package_m3_v2/data/qin_v4_2500.jsonl",
    "training_package_m3_v2/data/train.jsonl",
    "training_package_m3_v2/data/valid.jsonl",
)
PRIOR_HUMAN_ROOTS = (
    "eval/chat02/human/chat02e-v1/public",
    "eval/chat02/human/chat02e-v1/private",
    "eval/chat02/human/chat02e-v1/submissions",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FreezeVerificationError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _validate(value: dict[str, Any], schema_path: Path) -> None:
    schema = _load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FreezeVerificationError(f"missing artifact: {path}")
    return {"path": _relative(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _model_binding(path: Path, role: str) -> dict[str, Any]:
    manifest = _load_json(path)
    return {
        "model_id": manifest["model_id"],
        "role": role,
        "manifest": _artifact(path),
        "gguf": manifest["asset"],
    }


def _tree_commitment(relative: str) -> dict[str, Any]:
    root = ROOT / relative
    files = sorted(path for path in root.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return {
        "path": relative,
        "files": len(files),
        "sha256_mode": "sorted_relative_path_nul_file_digest",
        "sha256": digest.hexdigest(),
    }


def _case_inventory() -> dict[str, int]:
    def rows(relative: str) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (ROOT / relative).read_text(encoding="utf-8").splitlines() if line.strip()]

    single = rows("eval/chat01/suites/chat01_frozen_single_v1.jsonl")
    multi = rows("eval/chat01/suites/chat01_frozen_multiturn_v1.jsonl")
    human = rows("eval/chat01/suites/chat01_human_blind_v1.jsonl")
    return {
        "frozen_single": len(single),
        "frozen_multiturn": len(multi),
        "human_pair": sum(item.get("case_type") == "human_pair" for item in human),
        "human_long_session": sum(item.get("case_type") == "human_long_session" for item in human),
    }


def _canonical_contract_sha256(contract: dict[str, Any]) -> str:
    canonical = deepcopy(contract)
    canonical["freeze"]["contract_sha256"] = None
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_report() -> dict[str, Any]:
    suite = verify_manifest()
    report = scan_leakage(training_patterns=TRAINING_PATTERNS)
    report["report_id"] = "chat02f-v2500-leakage-evidence-v1"
    report["suite_binding"] = {
        "suite_id": suite["suite_id"],
        "manifest_path": _relative(SUITE_PATH),
        "manifest_file_sha256": sha256_file(SUITE_PATH),
        "manifest_self_sha256": suite["freeze"]["manifest_sha256"],
    }
    report["corpus_provenance"] = {
        "status": "local_candidate_unverified",
        "claim": "These three local files are the candidate v2500 corpus and split artifacts selected for contamination screening; the deployment package does not prove that the training machine consumed these exact hashes.",
        "strict_training_attribution": False,
        "recheck_trigger": "When a training-machine manifest or input hashes become available, compare them with every training_sources hash; any mismatch requires a new leakage scan and a new comparison contract version.",
    }
    blocked = report["summary"]["blocking_findings"] > 0
    report["decision"] = {
        "can_start_full_comparison": not blocked,
        "requires_manual_disposition": blocked,
        "reason": "Exact or normalized leakage blocks the full comparison until investigated." if blocked else "No exact, normalized, or threshold-level near-duplicate finding was detected.",
    }
    ordered = {
        key: report[key]
        for key in (
            "report_id", "schema_version", "created_at", "suite_binding", "corpus_provenance",
            "evaluation_sources", "training_sources", "algorithms", "findings", "summary", "status", "decision",
        )
    }
    _validate(ordered, REPORT_SCHEMA)
    return ordered


def build_contract(report: dict[str, Any]) -> dict[str, Any]:
    suite = verify_manifest()
    blocked = report["status"] == "blocked"
    contract = {
        "contract_id": "chat02f-comparison-contract-v1",
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "frozen_blocked" if blocked else "frozen_ready",
        "only_active_variable": "model_weights",
        "models": [
            _model_binding(BASE_MANIFEST_PATH, "base"),
            _model_binding(CANDIDATE_MANIFEST_PATH, "candidate"),
        ],
        "suite": {
            "suite_id": suite["suite_id"],
            "manifest": _artifact(SUITE_PATH),
            "manifest_self_sha256": suite["freeze"]["manifest_sha256"],
            "verify_command": "python -m eval.chat01.freeze --verify",
        },
        "prompt_renderer": _artifact(PROMPT_PATH),
        "case_inventory": _case_inventory(),
        "run_order": {
            "policy": "precommitted_seed_then_sequential_models",
            "sequence": ["qwen3-4b-base-q4_k_m", "qinweixi-v2500-final-q4_k_m"],
            "between_models": ["shutdown_owned_server", "verify_port_released", "verify_gpu_released"],
        },
        "leakage_evidence": _artifact(REPORT_PATH),
        "excluded_prior_human_assets": {
            "policy": "excluded_from_chat02f_denominators_and_assignments",
            "roots": [_tree_commitment(path) for path in PRIOR_HUMAN_ROOTS],
        },
        "gates": {
            "suite_verified": True,
            "model_assets_verified": True,
            "prompt_frozen": True,
            "leakage_status": report["status"],
            "can_start_chat02f_c": not blocked,
            "blockers": [
                "One exact training/evaluation text match requires disposition before CHAT-02F-C/D."
            ] if blocked else [],
        },
        "freeze": {
            "immutable": True,
            "hash_algorithm": "sha256",
            "hash_mode": "canonical_json_with_null_contract_sha256",
            "contract_sha256": None,
        },
    }
    contract["freeze"]["contract_sha256"] = _canonical_contract_sha256(contract)
    _validate(contract, CONTRACT_SCHEMA)
    return contract


def freeze_current() -> dict[str, Any]:
    if CONTRACT_PATH.exists():
        raise FreezeVerificationError("CHAT-02F comparison contract v1 already exists and is immutable")
    report = build_report()
    _write_json(REPORT_PATH, report)
    contract = build_contract(report)
    _write_json(CONTRACT_PATH, contract)
    return verify_frozen()


def verify_frozen() -> dict[str, Any]:
    verify_manifest()
    report = _load_json(REPORT_PATH)
    contract = _load_json(CONTRACT_PATH)
    _validate(report, REPORT_SCHEMA)
    _validate(contract, CONTRACT_SCHEMA)
    if contract["freeze"]["contract_sha256"] != _canonical_contract_sha256(contract):
        raise FreezeVerificationError("CHAT-02F comparison contract self SHA256 changed")
    for source in report["evaluation_sources"] + report["training_sources"]:
        verify_file(ROOT, source)
    for artifact in (contract["suite"]["manifest"], contract["prompt_renderer"], contract["leakage_evidence"]):
        verify_file(ROOT, artifact)
    for model in contract["models"]:
        verify_file(ROOT, model["manifest"])
        verify_file(ROOT, model["gguf"])
    if contract["case_inventory"] != _case_inventory():
        raise FreezeVerificationError("CHAT-02F case inventory changed")
    if contract["excluded_prior_human_assets"]["roots"] != [_tree_commitment(path) for path in PRIOR_HUMAN_ROOTS]:
        raise FreezeVerificationError("excluded CHAT-02E human asset commitment changed")
    blocked = report["summary"]["blocking_findings"] > 0
    if blocked != (report["status"] == "blocked"):
        raise FreezeVerificationError("leakage status disagrees with findings")
    if contract["gates"]["can_start_chat02f_c"] == blocked:
        raise FreezeVerificationError("comparison gate disagrees with leakage status")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze or verify CHAT-02F-B leakage evidence and comparison contract.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        contract = freeze_current() if args.freeze else verify_frozen()
    except (FreezeVerificationError, OSError, ValueError) as exc:
        print(f"CHAT-02F-B freeze verification failed: {exc}")
        return 2
    print(json.dumps({
        "contract_id": contract["contract_id"],
        "status": contract["status"],
        "can_start_chat02f_c": contract["gates"]["can_start_chat02f_c"],
        "blockers": contract["gates"]["blockers"],
        "contract_sha256": contract["freeze"]["contract_sha256"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
