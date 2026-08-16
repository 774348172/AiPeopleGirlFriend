from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "eval/chat01/suites/chat01_frozen_single_v1.jsonl"
OUTPUT_PATH = ROOT / "eval/diagnostic120/cases_v1.jsonl"
MANIFEST_PATH = ROOT / "eval/diagnostic120/cases_manifest_v1.json"
SELECTION_SEED = "aipeople-diagnostic120-v1"
EXCLUDED_CASE_IDS = frozenset({"frozen.identity.name"})
QUOTAS = {
    "identity_timeline": 20,
    "relationship_boundary": 16,
    "unknown_reality": 12,
    "safety_health": 12,
    "general_capability": 30,
    "daily_relevance": 10,
    "emotion": 10,
    "style": 10,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def select_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for category, quota in QUOTAS.items():
        candidates = [
            case
            for case in cases
            if case["category"] == category and case["case_id"] not in EXCLUDED_CASE_IDS
        ]
        ranked = sorted(
            candidates,
            key=lambda case: hashlib.sha256(
                f"{SELECTION_SEED}\x1f{case['case_id']}".encode("utf-8")
            ).hexdigest(),
        )
        if len(ranked) < quota:
            raise ValueError(f"not enough cases for {category}: {len(ranked)} < {quota}")
        selected.extend(ranked[:quota])

    selected.sort(key=lambda case: case["case_id"])
    if len(selected) != 120 or len({case["case_id"] for case in selected}) != 120:
        raise ValueError("selection must contain 120 unique cases")
    return [
        {
            **case,
            "generation": {
                **case["generation"],
                "seed_set": [42],
            },
        }
        for case in selected
    ]


def build() -> dict[str, Any]:
    cases = select_cases(_load_jsonl(SOURCE_PATH))
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "".join(
            json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n"
            for case in cases
        ),
        encoding="utf-8",
        newline="\n",
    )
    counts = Counter(case["category"] for case in cases)
    manifest = {
        "diagnostic_id": "qinweixi-base-vs-v2500-simple120-v1",
        "status": "diagnostic_not_release_gate",
        "purpose": "Compare base Qwen3-4B and Qin Weixi v2500 with the same 120 questions and the same runtime contract.",
        "source": {
            "path": str(SOURCE_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(SOURCE_PATH),
            "case_count": 240,
        },
        "selection": {
            "method": "per_category_sha256_rank",
            "seed": SELECTION_SEED,
            "excluded_case_ids": sorted(EXCLUDED_CASE_IDS),
            "quotas": QUOTAS,
            "case_count": len(cases),
            "category_counts": dict(sorted(counts.items())),
            "generation_seed": 42,
        },
        "cases": {
            "path": str(OUTPUT_PATH.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256_file(OUTPUT_PATH),
        },
        "comparison": {
            "only_active_variable": "model_weights",
            "same_runtime_prompt": True,
            "same_llama_cpp_profile": True,
            "execution": "sequential_base_then_v2500",
            "semantic_quality_requires_human_review": True,
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
