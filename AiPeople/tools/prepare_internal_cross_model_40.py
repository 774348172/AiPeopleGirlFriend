from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.world_mind import (
    CharacterPackagePromptComposer,
    RuntimeSessionIdentity,
    WorldMindRuntimeConfig,
)


SUITE_ROOT = ROOT / "eval" / "baiweixi_quality"
OUTPUT_ROOT = ROOT / "eval" / "cross_model_internal_40"
FORMAL_ATTEMPTS_PATH = (
    SUITE_ROOT
    / "runs"
    / "baiweixi-quality-formal-20260811"
    / "attempts_raw.jsonl"
)
SELECTION_SALT = "baiweixi-gpt56sol-internal40-v1"
QUOTAS = {
    "identity_canon": 7,
    "world_canon": 6,
    "unknown_boundaries": 6,
    "relationship_pacing": 5,
    "ability_limits": 5,
    "single_world": 5,
    "free_dialogue": 6,
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _selection_rank(case_id: str) -> str:
    return hashlib.sha256(f"{SELECTION_SALT}|{case_id}".encode()).hexdigest()


def _runtime_config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
    )


def _select_cases() -> list[dict[str, Any]]:
    cases = [
        case
        for case in _read_jsonl(SUITE_ROOT / "cases" / "frozen_single_v1.jsonl")
        if case["evaluation_layer"] == "character_direct"
    ]
    selected: list[dict[str, Any]] = []
    for category, quota in QUOTAS.items():
        candidates = [case for case in cases if case["category"] == category]
        candidates.sort(key=lambda case: _selection_rank(str(case["case_id"])))
        if len(candidates) < quota:
            raise RuntimeError(f"not enough cases for {category}: {len(candidates)} < {quota}")
        selected.extend(candidates[:quota])
    selected.sort(key=lambda case: (str(case["category"]), str(case["case_id"])))
    if len(selected) != 40 or len({case["case_id"] for case in selected}) != 40:
        raise RuntimeError("selection must contain exactly 40 unique cases")
    return selected


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    selected = _select_cases()
    prompt = CharacterPackagePromptComposer(_runtime_config()).compose(
        RuntimeSessionIdentity(
            save_id="internal_cross_model_40",
            world_id="songjiangfu",
            protagonist_id="protagonist",
            active_character_id="baiweixi",
            conversation_id="internal_cross_model_40",
        )
    ).system_prompt
    prompt_path = OUTPUT_ROOT / "shared_character_prompt_v1.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    candidate_inputs = [
        {
            "ordinal": index,
            "case_id": case["case_id"],
            "category": case["category"],
            "risk": case["risk"],
            "user_text": case["turns"][0]["user_text"],
            "max_new_tokens": case["generation"]["max_new_tokens"],
        }
        for index, case in enumerate(selected, 1)
    ]
    input_path = OUTPUT_ROOT / "candidate_inputs_v1.jsonl"
    _write_jsonl(input_path, candidate_inputs)

    formal_attempts = {
        str(item["attempt_key"]): item for item in _read_jsonl(FORMAL_ATTEMPTS_PATH)
    }
    local_rows: list[dict[str, object]] = []
    for item in candidate_inputs:
        attempt_key = f"{item['case_id']}::seed=42"
        attempt = formal_attempts.get(attempt_key)
        if attempt is None or not attempt.get("success"):
            raise RuntimeError(f"missing successful local baseline: {attempt_key}")
        turn = attempt["turn_results"][0]
        local_rows.append(
            {
                "ordinal": item["ordinal"],
                "case_id": item["case_id"],
                "model": "ollama/baiweixi:latest",
                "model_revision": "09d23dc30422a78343d65dd1c566f92287917c82856c6c1aa1126b2c43dd58fa",
                "seed": 42,
                "response": turn["response"],
                "elapsed_seconds": attempt["elapsed_seconds"],
                "source_attempt_key": attempt_key,
            }
        )
    local_path = OUTPUT_ROOT / "local_baiweixi_seed42.jsonl"
    _write_jsonl(local_path, local_rows)

    manifest = {
        "schema_version": 1,
        "experiment_id": "baiweixi-vs-gpt56sol-internal40-v1",
        "status": "prepared",
        "scope": "Codex-internal qualitative Lane R comparison; not an OpenAI API runtime test",
        "selection": {
            "source_suite": "baiweixi-quality-v1",
            "source_manifest_sha256": "182ed7603551bca7e4acd0e1a2c4c2acafaec4f30d8bba43408179ad1f75c3d9",
            "eligible_layer": "character_direct",
            "selection_salt": SELECTION_SALT,
            "selection_algorithm": "lowest sha256(salt|case_id) within each category quota",
            "category_quotas": QUOTAS,
            "risk_counts": dict(sorted(Counter(case["risk"] for case in selected).items())),
            "case_ids": [case["case_id"] for case in selected],
        },
        "shared_inputs": {
            "character_prompt_path": prompt_path.relative_to(ROOT).as_posix(),
            "character_prompt_sha256": _sha256(prompt_path),
            "candidate_inputs_path": input_path.relative_to(ROOT).as_posix(),
            "candidate_inputs_sha256": _sha256(input_path),
            "oracle_withheld_from_candidates": True,
        },
        "candidates": {
            "local": {
                "model": "baiweixi:latest",
                "ollama_digest": "09d23dc30422a78343d65dd1c566f92287917c82856c6c1aa1126b2c43dd58fa",
                "seed": 42,
                "responses_path": local_path.relative_to(ROOT).as_posix(),
                "responses_sha256": _sha256(local_path),
            },
            "gpt": {
                "model": "gpt-5.6-sol",
                "surface": "Codex internal sub-agent",
                "seed": None,
                "responses_path": "eval/cross_model_internal_40/gpt5_6_sol_internal.jsonl",
            },
        },
        "limitations": [
            "This does not exercise an OpenAI API backend or V6 structured five-mode runtime.",
            "The Codex internal model has host instructions that cannot be made identical to an API system message.",
            "GPT sampling seed, API latency, token usage, cost, and provider response metadata are unavailable.",
            "Only independent character_direct cases are included; no multi-turn memory conclusion is allowed.",
        ],
    }
    manifest_path = OUTPUT_ROOT / "selection_manifest_v1.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "cases": len(selected),
                "categories": QUOTAS,
                "risks": manifest["selection"]["risk_counts"],
                "output_root": str(OUTPUT_ROOT),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
