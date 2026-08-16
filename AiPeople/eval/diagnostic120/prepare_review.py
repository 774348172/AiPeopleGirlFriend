from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .build_cases import ROOT, sha256_file


RESULTS_ROOT = ROOT / "eval/diagnostic120/reports/simple120-v1"
SOURCE_PACKETS = RESULTS_ROOT / "pairs_public.jsonl"
SOURCE_ASSIGNMENTS = RESULTS_ROOT / "assignments_private.jsonl"
PACKAGE_ROOT = ROOT / "eval/diagnostic120/human/simple120-review-v1"
PUBLIC_ROOT = PACKAGE_ROOT / "public"
PRIVATE_ROOT = PACKAGE_ROOT / "private"
PACKETS_PATH = PUBLIC_ROOT / "ab_packets.jsonl"
BLANKS_PATH = PUBLIC_ROOT / "blank_ballots.jsonl"
ASSIGNMENTS_PATH = PRIVATE_ROOT / "assignments.jsonl"
CONTRACT_PATH = PACKAGE_ROOT / "review_contract.json"
RUBRIC_PATH = ROOT / "eval/chat01/rubrics/quality_rubric_v1.json"
BALLOT_SCHEMA_PATH = ROOT / "eval/chat02/reviewer/human_ballot_v2.schema.json"
SELECTION_SEED = "diagnostic120-human40-v1"
QUOTAS = {
    "identity_timeline": 7,
    "relationship_boundary": 5,
    "unknown_reality": 4,
    "safety_health": 4,
    "general_capability": 10,
    "daily_relevance": 3,
    "emotion": 3,
    "style": 4,
}
TRAINED_A_TARGET = {
    "identity_timeline": 4,
    "relationship_boundary": 2,
    "unknown_reality": 2,
    "safety_health": 2,
    "general_capability": 5,
    "daily_relevance": 2,
    "emotion": 1,
    "style": 2,
}
DIMENSIONS = (
    "relevance",
    "correctness",
    "persona_fit",
    "relationship_naturalness",
    "emotion_fit",
    "naturalness",
)
TRAINED_MODEL_ID = "qinweixi-v2500-final-q4_k_m"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values),
        encoding="utf-8",
        newline="\n",
    )


def _rank(unit_id: str) -> str:
    return hashlib.sha256(f"{SELECTION_SEED}\x1f{unit_id}".encode("utf-8")).hexdigest()


def select_units(
    packets: list[dict[str, Any]], assignments: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    assignment_by_id = {item["unit_id"]: item for item in assignments}
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for category, quota in QUOTAS.items():
        candidates = [
            (packet, assignment_by_id[packet["unit_id"]])
            for packet in packets
            if packet["category"] == category
        ]
        trained_a = sorted(
            (item for item in candidates if item[1]["candidate_a_model"] == TRAINED_MODEL_ID),
            key=lambda item: _rank(item[0]["unit_id"]),
        )
        base_a = sorted(
            (item for item in candidates if item[1]["candidate_a_model"] != TRAINED_MODEL_ID),
            key=lambda item: _rank(item[0]["unit_id"]),
        )
        trained_target = TRAINED_A_TARGET[category]
        if len(trained_a) < trained_target or len(base_a) < quota - trained_target:
            raise ValueError(f"not enough A/B-balanced candidates for {category}")
        selected.extend(trained_a[:trained_target])
        selected.extend(base_a[: quota - trained_target])
    selected.sort(key=lambda item: _rank(item[0]["unit_id"] + "\x1forder"))
    if len(selected) != 40 or len({item[0]["unit_id"] for item in selected}) != 40:
        raise ValueError("review selection must contain 40 unique units")
    return selected


def _asset(path: Path, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def build() -> dict[str, Any]:
    selected = select_units(_load_jsonl(SOURCE_PACKETS), _load_jsonl(SOURCE_ASSIGNMENTS))
    packets: list[dict[str, Any]] = []
    blanks: list[dict[str, Any]] = []
    private: list[dict[str, Any]] = []
    for index, (source, secret) in enumerate(selected, start=1):
        unit_id = source["unit_id"]
        output_a = "output." + hashlib.sha256(f"{unit_id}\x1fA".encode()).hexdigest()[:24]
        output_b = "output." + hashlib.sha256(f"{unit_id}\x1fB".encode()).hexdigest()[:24]
        packets.append(
            {
                "unit_id": unit_id,
                "generation_seed": 42,
                "prompt": source["prompt"],
                "scenario_class": source["category"],
                "evaluation_focus": list(DIMENSIONS),
                "presentation": "AB",
                "candidate_a": {"output_id": output_a, "text": source["candidate_a"]},
                "candidate_b": {"output_id": output_b, "text": source["candidate_b"]},
            }
        )
        blanks.append(
            {
                "submission_status": "blank",
                "ballot_id": f"diagnostic120.ballot.{index:03d}",
                "suite_id": "qinweixi-base-vs-v2500-simple120-v1",
                "rubric_id": "chat01-quality-rubric-v1",
                "unit_id": unit_id,
                "reviewer_id": None,
                "presentation": "AB",
                "candidate_a_output_id": output_a,
                "candidate_b_output_id": output_b,
                "dimension_scores": {
                    dimension: {"candidate_a": None, "candidate_b": None}
                    for dimension in DIMENSIONS
                },
                "verdict": None,
                "failure_reasons": {"candidate_a": [], "candidate_b": []},
                "both_unacceptable": None,
                "rationale": None,
                "submitted_at": None,
                "revealed": False,
            }
        )
        private.append(
            {
                "unit_id": unit_id,
                "candidate_a_output_id": output_a,
                "candidate_b_output_id": output_b,
                "candidate_a_model": secret["candidate_a_model"],
                "candidate_b_model": secret["candidate_b_model"],
            }
        )

    _write_jsonl(PACKETS_PATH, packets)
    _write_jsonl(BLANKS_PATH, blanks)
    _write_jsonl(ASSIGNMENTS_PATH, private)
    counts = Counter(packet["scenario_class"] for packet in packets)
    trained_a_count = sum(item["candidate_a_model"] == TRAINED_MODEL_ID for item in private)
    contract = {
        "contract_id": "diagnostic120-human40-reviewer-v1",
        "schema_version": 3,
        "suite_id": "qinweixi-base-vs-v2500-simple120-v1",
        "package_id": "diagnostic120-human40-v1",
        "source_units": 40,
        "expected_units": 40,
        "selected_unit_ids": [packet["unit_id"] for packet in packets],
        "selection": {
            "source_units": 120,
            "method": "category_quota_plus_hidden_side_balance",
            "seed": SELECTION_SEED,
            "category_counts": dict(sorted(counts.items())),
            "candidate_a_balance": {"v2500": trained_a_count, "base": 40 - trained_a_count},
        },
        "assets": [
            _asset(PACKETS_PATH, "packets"),
            _asset(BLANKS_PATH, "blank_ballots"),
            _asset(RUBRIC_PATH, "rubric"),
            _asset(BALLOT_SCHEMA_PATH, "ballot_schema"),
        ],
        "submission_root": "eval/diagnostic120/human/simple120-review-v1/submissions",
        "network": {"host": "127.0.0.1", "default_port": 18121},
        "privacy": {
            "public_only": True,
            "private_assignment_access": False,
            "reveal_supported": False,
        },
        "rationale_required": False,
    }
    CONTRACT_PATH.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return contract


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
