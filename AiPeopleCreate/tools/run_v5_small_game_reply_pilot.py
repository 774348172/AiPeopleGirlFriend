"""Run the bounded 32-sample GAME_REPLY generator pilot.

This is a candidate-only smoke gate. It reuses the V5 scenario builder,
teacher/audit adapter and hard gates; it never writes a training split.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_v5_pilot_dataset import (
    ROOT,
    TRAIN_CONCEPTS,
    build_scenarios,
    generate_grounded,
    inspect_grounded,
    _write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "训练数据" / "baiweixi_v5_small_game_reply_pilot_20260911",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="qwen2.5:14b-instruct-q4_K_M")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    # Four concepts x eight frozen task types = 32 candidates.
    scenarios = build_scenarios(TRAIN_CONCEPTS[:4], sealed=False)
    if len(scenarios) != 32:
        raise RuntimeError(f"small pilot must contain 32 scenarios, got {len(scenarios)}")
    if any(row["model_view"].get("mode") != "GAME_REPLY" for row in scenarios):
        raise RuntimeError("small pilot contains a non-GAME_REPLY scenario")

    inputs = args.output_root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    _write_jsonl(inputs / "game_reply_scenarios.jsonl", scenarios)
    candidates = generate_grounded(
        scenarios,
        inputs / "game_reply_candidates.jsonl",
        base_url=args.base_url,
        model_name=args.model,
        workers=args.workers,
        max_attempts=2,
    )
    inspection = inspect_grounded(candidates, inputs / "game_reply_spot_check.json")
    manifest = {
        "schema_version": "aip.runtime_grounded_small_game_reply_manifest.v1",
        "candidate_only": True,
        "training_started": False,
        "model_view_mode": "GAME_REPLY",
        "render_profile_id": "GAME_REPLY_CONTEXT_V1",
        "teacher_oracle_view_exposed": False,
        "teacher_and_audit_are_separate_calls": True,
        "scenario_count": len(scenarios),
        "candidate_count": len(candidates),
        "spot_check": inspection,
        "files": [
            "inputs/game_reply_scenarios.jsonl",
            "inputs/game_reply_candidates.jsonl",
            "inputs/game_reply_spot_check.json",
        ],
    }
    (args.output_root / "pilot_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output_root), **manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
