from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.chat01.freeze import FreezeVerificationError, sha256_file, verify_file
from eval.chat01v4.freeze import MANIFEST as SUITE_MANIFEST, verify as verify_suite_v4
from eval.chat02f.freeze import _artifact, _load_json
from eval.chat02f.freeze_v2 import CONTRACT_V2_PATH, DISPOSITION_PATH


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "eval/chat02f/comparison_contract_v3.json"
PROFILE_PATH = ROOT / "eval/chat02f/execution_profile_v2.json"
SMOKE_PATH = ROOT / "eval/chat02f/smoke_cases_v2.jsonl"
PROMPT_PATH = ROOT / "runtime/_prompt.py"
OLD_PROFILE_PATH = ROOT / "eval/chat02f/execution_profile_v1.json"
MODEL_MANIFESTS = (
    ROOT / "eval/chat02/models/qwen3-4b-base-q4_k_m.json",
    ROOT / "eval/chat02f/models/qinweixi-v2500-final-q4_k_m.json",
)


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _canonical(value: dict[str, Any], field: str) -> str:
    copied = deepcopy(value)
    copied["freeze"][field] = None
    raw = json.dumps(copied, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in SMOKE_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def _verified_old_contract() -> dict[str, Any]:
    """Verify immutable v2 evidence while deliberately ignoring its superseded canon binding."""
    old = _load_json(CONTRACT_V2_PATH)
    if old["freeze"]["contract_sha256"] != _canonical(old, "contract_sha256"):
        raise FreezeVerificationError("superseded comparison contract v2 self hash changed")
    for item in (old["leakage_evidence"], old["leakage_disposition"]):
        verify_file(ROOT, item)
    for model in old["models"]:
        verify_file(ROOT, model["manifest"])
        verify_file(ROOT, model["gguf"])
    return old


def build_contract(now: str) -> dict[str, Any]:
    old = _verified_old_contract()
    suite = verify_suite_v4()
    return {
        "contract_id": "chat02f-comparison-contract-v3", "schema_version": 3, "created_at": now,
        "status": "frozen_ready_with_diagnostic_exclusion",
        "supersedes": _artifact(CONTRACT_V2_PATH),
        "change_scope": {
            "accepted_canon": "chat01-canon-v3",
            "shared_runtime_fix": "evidence boundary, physical capability, urgent safety, and prompt-extraction resistance",
            "evaluation_content": "240/30/60/8 inventory and all frozen CHAT-01 case/rubric/oracle bytes unchanged",
        },
        "only_active_variable": "model_weights",
        "models": old["models"],
        "suite": {
            "suite_id": suite["suite_id"], "manifest": _artifact(SUITE_MANIFEST),
            "manifest_self_sha256": suite["freeze"]["manifest_sha256"],
            "verify_command": "python -m eval.chat01v4.freeze --verify",
        },
        "prompt_renderer": _artifact(PROMPT_PATH),
        "case_inventory": old["case_inventory"],
        "analysis_population": old["analysis_population"],
        "run_order": old["run_order"],
        "leakage_evidence": old["leakage_evidence"],
        "leakage_disposition": old["leakage_disposition"],
        "excluded_prior_human_assets": old["excluded_prior_human_assets"],
        "gates": old["gates"],
        "freeze": {"immutable": True, "hash_algorithm": "sha256", "hash_mode": "canonical_json_with_null_contract_sha256", "contract_sha256": None},
    }


def build_profile(now: str, contract: dict[str, Any]) -> dict[str, Any]:
    profile = deepcopy(_load_json(OLD_PROFILE_PATH))
    profile.update({"profile_id": "chat02f-shared-llamacpp-b10256-v2", "schema_version": 2, "created_at": now})
    profile["comparison_contract"] = {
        "contract_id": contract["contract_id"], "contract": _artifact(CONTRACT_PATH),
        "contract_self_sha256": contract["freeze"]["contract_sha256"],
        "only_active_variable": "model_weights",
        "model_ids": [model["model_id"] for model in contract["models"]],
        "run_order_policy": contract["run_order"]["policy"],
    }
    profile["request_contract"]["prompt_renderer"] = _artifact(PROMPT_PATH)
    profile["suite_contract"] = {
        "suite_id": contract["suite"]["suite_id"], "manifest": contract["suite"]["manifest"],
        "manifest_self_sha256": contract["suite"]["manifest_self_sha256"],
    }
    counts = Counter(item["category"] for item in _cases())
    profile["smoke_contract"] = {
        "suite_id": "chat02f-directed-smoke-v2", "cases": _artifact(SMOKE_PATH),
        "schema": _artifact(ROOT / "eval/chat02f/schema/smoke_case.schema.json"),
        "case_count": 12, "category_counts": dict(counts), "frozen_before_outputs": True,
        "automatic_rule_revision": "oil-fire prohibition distinguishes affirmative water advice from correct negation",
    }
    profile["readiness"]["next_real_model_run"] = "chat02f_c_smoke_v2"
    return profile


def freeze() -> dict[str, Any]:
    if CONTRACT_PATH.exists() or PROFILE_PATH.exists():
        raise FreezeVerificationError("CHAT-02F v3/v2 assets already exist and are immutable")
    _verified_old_contract()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    contract = build_contract(now)
    contract["freeze"]["contract_sha256"] = _canonical(contract, "contract_sha256")
    _write(CONTRACT_PATH, contract)
    _write(PROFILE_PATH, build_profile(now, contract))
    return verify()


def verify() -> dict[str, Any]:
    _verified_old_contract()
    suite = verify_suite_v4()
    contract = _load_json(CONTRACT_PATH)
    profile = _load_json(PROFILE_PATH)
    if contract["freeze"]["contract_sha256"] != _canonical(contract, "contract_sha256"):
        raise FreezeVerificationError("comparison contract v3 self hash changed")
    for item in (contract["supersedes"], contract["suite"]["manifest"], contract["prompt_renderer"], contract["leakage_evidence"], contract["leakage_disposition"]):
        verify_file(ROOT, item)
    if contract["suite"]["manifest_self_sha256"] != suite["freeze"]["manifest_sha256"]:
        raise FreezeVerificationError("suite v4 self hash mismatch")
    old = _verified_old_contract()
    for key in ("models", "case_inventory", "analysis_population", "run_order", "leakage_evidence", "leakage_disposition", "excluded_prior_human_assets"):
        if contract[key] != old[key]:
            raise FreezeVerificationError(f"v3 changed preserved comparison field: {key}")
    cases = _cases()
    if len(cases) != 12 or len({item["case_id"] for item in cases}) != 12:
        raise FreezeVerificationError("smoke v2 must contain 12 unique cases")
    for item in (profile["comparison_contract"]["contract"], profile["request_contract"]["prompt_renderer"], profile["suite_contract"]["manifest"], profile["smoke_contract"]["cases"], profile["smoke_contract"]["schema"]):
        verify_file(ROOT, item)
    if profile["comparison_contract"]["contract_self_sha256"] != contract["freeze"]["contract_sha256"]:
        raise FreezeVerificationError("profile does not bind contract v3 self hash")
    if profile["smoke_contract"]["frozen_before_outputs"] is not True:
        raise FreezeVerificationError("smoke inputs were not frozen before outputs")
    for manifest_path in MODEL_MANIFESTS:
        manifest = _load_json(manifest_path)
        verify_file(ROOT, manifest["asset"])
    return contract


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        result = freeze() if args.freeze else verify()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "verified", "contract_id": result["contract_id"], "contract_sha256": result["freeze"]["contract_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
