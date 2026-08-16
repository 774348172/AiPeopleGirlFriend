from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from eval.chat01.freeze import FreezeVerificationError, verify_file
from eval.chat01.leakage_guard import sha256_file
from eval.chat02f import runner as core
from eval.chat02f.freeze import _load_json
from eval.chat02f.freeze_v3 import CONTRACT_PATH, PROFILE_PATH, SMOKE_PATH, verify as verify_contract


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = Path(__file__).resolve()
REPORTS_ROOT = ROOT / "eval/chat02f/reports"
MODEL_MANIFESTS = {
    "qwen3-4b-base-q4_k_m": ROOT / "eval/chat02/models/qwen3-4b-base-q4_k_m.json",
    "qinweixi-v2500-final-q4_k_m": ROOT / "eval/chat02f/models/qinweixi-v2500-final-q4_k_m.json",
}
RUN_IDS = {
    "qwen3-4b-base-q4_k_m": "chat02fc-smoke-base-v2",
    "qinweixi-v2500-final-q4_k_m": "chat02fc-smoke-v2500-v2",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_profile() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    contract = verify_contract()
    profile = _load_json(PROFILE_PATH)
    for item in (
        profile["comparison_contract"]["contract"], profile["backend"]["server"],
        profile["backend"]["runtime_adapter"], profile["request_contract"]["prompt_renderer"],
        profile["suite_contract"]["manifest"], profile["smoke_contract"]["cases"],
        profile["smoke_contract"]["schema"],
    ):
        verify_file(ROOT, item)
    if profile["comparison_contract"]["contract_self_sha256"] != contract["freeze"]["contract_sha256"]:
        raise FreezeVerificationError("profile does not bind contract v3")
    cases = _load_jsonl(SMOKE_PATH)
    if len(cases) != 12 or len({item["case_id"] for item in cases}) != 12:
        raise FreezeVerificationError("smoke v2 inventory changed")
    manifests: dict[str, dict[str, Any]] = {}
    for binding in contract["models"]:
        path = MODEL_MANIFESTS[binding["model_id"]]
        verify_file(ROOT, binding["manifest"])
        manifest = _load_json(path)
        verify_file(ROOT, manifest["asset"])
        manifests[binding["model_id"]] = manifest
    return profile, cases, manifests


def _bind_core() -> None:
    core.PROFILE_PATH = PROFILE_PATH
    core.SMOKE_PATH = SMOKE_PATH
    core.CONTRACT_V2_PATH = CONTRACT_PATH
    core.RUNNER_PATH = RUNNER_PATH
    core.RUN_IDS = RUN_IDS


async def run_both(reports_root: Path = REPORTS_ROOT) -> dict[str, Any]:
    profile, cases, manifests = verify_profile()
    _bind_core()
    if core._port_in_use(profile["launch"]["host"], profile["launch"]["port"]):
        raise RuntimeError("shared llama.cpp port is already in use")
    reports_root.mkdir(parents=True, exist_ok=True)
    summaries = []
    for model_id in profile["comparison_contract"]["model_ids"]:
        _path, summary = await core.run_model(
            model_id=model_id, manifest=manifests[model_id], profile=profile,
            cases=cases, reports_root=reports_root,
        )
        summaries.append(summary)
        lifecycle = summary["lifecycle"]
        if lifecycle["error"] or not lifecycle["port_released"] or not lifecycle["llama_gpu_process_released"]:
            raise RuntimeError(f"model lifecycle did not release cleanly: {model_id}")
    comparison = {
        "comparison_id": "chat02fc-smoke-comparison-v2", "profile_id": profile["profile_id"],
        "model_order": profile["comparison_contract"]["model_ids"], "models": summaries,
        "automatic_gate_passed": all(item["automatic_gate_passed"] for item in summaries),
        "manual_review_status": "pending",
        "next_stage": "manual_smoke_review" if all(item["automatic_gate_passed"] for item in summaries) else "blocked",
        "bindings": {
            "profile_sha256": sha256_file(PROFILE_PATH), "contract_sha256": sha256_file(CONTRACT_PATH),
            "contract_self_sha256": profile["comparison_contract"]["contract_self_sha256"],
            "runner_sha256": sha256_file(RUNNER_PATH), "smoke_cases_sha256": sha256_file(SMOKE_PATH),
        },
    }
    core._write_json(reports_root / "chat02fc-smoke-comparison-v2.json", comparison)
    return comparison


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-root", type=Path, default=REPORTS_ROOT)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.verify_only:
            profile, cases, manifests = verify_profile()
            result = {"status": "verified", "profile_id": profile["profile_id"], "cases": len(cases), "models": list(manifests)}
        else:
            result = asyncio.run(run_both(args.reports_root))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
