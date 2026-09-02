"""Combine the frozen targeted-Adapter run and full-mixed-Adapter run for review."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--targeted-report", type=Path, required=True)
    parser.add_argument("--full-mixed-report", type=Path, required=True)
    parser.add_argument("--targeted-user-review", type=Path)
    args = parser.parse_args()

    targeted = read_json(args.targeted_report.resolve())
    full_mixed = read_json(args.full_mixed_report.resolve())
    old_cases = targeted.get("cases", [])
    new_cases = full_mixed.get("cases", [])
    if len(old_cases) != 60 or len(new_cases) != 60:
        raise ValueError("both reports must contain the frozen 60-turn trajectory")

    cases: list[dict[str, Any]] = []
    for old, new in zip(old_cases, new_cases, strict=True):
        stable_fields = ("ordinal", "question", "visible_memory", "world", "review_focus")
        if any(old.get(field) != new.get(field) for field in stable_fields):
            raise ValueError(f"frozen input mismatch at turn {old.get('ordinal')}")
        cases.append(
            {
                "ordinal": old["ordinal"],
                "category": old["category"],
                "condition": old.get("phase", old["category"]),
                "question": old["question"],
                "visible_memory": old["visible_memory"],
                "review_focus": old["review_focus"],
                "messages": old["messages"],
                "messages_by_arm": {
                    "base": old["messages"],
                    "preference": new["messages"],
                },
                "answers": {
                    "base": old["answer"],
                    "preference": new["answer"],
                },
            }
        )

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": 1,
        "scope": "gemma4_targeted_vs_full_mixed_lora_stateful60",
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "arms": {
            "base": "A 当前 V5 定向 Adapter（已审核 56/60）",
            "preference": "B 全量混合数据新 Adapter（2189 段）",
        },
        "controls": {
            "same_frozen_player_trajectory": True,
            "same_system_policy_and_memory_projection": True,
            "same_decoding_and_seed_per_turn": True,
            "stateful_histories_are_arm_specific": True,
            "note": "两侧各自生成的回复会进入本侧后续上下文，这是连续对话的真实产品行为。",
        },
        "training": {
            "full_mixed_rows": 2189,
            "v4_full_rows": 1973,
            "v5_grounded_rows": 192,
            "v5_supplement_rows": 24,
            "full_mixed_adapter_sha256": full_mixed["model_identity"]["artifact_sha256"],
        },
        "speed": {
            "base": targeted.get("speed"),
            "preference": full_mixed.get("speed"),
        },
        "cases": cases,
    }
    write_json(output_dir / "report.json", report)
    if args.targeted_user_review is not None:
        old_review = read_json(args.targeted_user_review.resolve())
        migrated: dict[str, Any] = {}
        for ordinal, review in old_review.get("reviews", {}).items():
            migrated[ordinal] = {
                "base": {
                    **review,
                    "arm": "base",
                    "migrated_from": str(args.targeted_user_review.resolve()),
                }
            }
        write_json(
            output_dir / "user_review.json",
            {
                "schema_version": 1,
                "source_report": str(output_dir / "report.json"),
                "created_at": datetime.now().astimezone().isoformat(),
                "updated_at": None,
                "reviews": migrated,
            },
        )
    print(f"REPORT={output_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
