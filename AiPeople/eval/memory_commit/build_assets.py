from __future__ import annotations

import json
from pathlib import Path

from runtime._memory_store import DECISION_REASON_CODES, RUN_STATES


ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "eval" / "memory_commit"


def validate_assets() -> None:
    profile = _load(EVAL_DIR / "mem04_profile_v1.json")
    valid = _load(EVAL_DIR / "fixtures" / "valid" / "commit_cases_v1.json")
    invalid = _load(EVAL_DIR / "fixtures" / "invalid" / "commit_cases_v1.json")
    if profile["run_states"] != sorted(RUN_STATES):
        raise ValueError("MEM-04 run states drifted from runtime")
    if profile["decision_reason_codes"] != sorted(DECISION_REASON_CODES):
        raise ValueError("MEM-04 decision reasons drifted from runtime")
    if profile["append_only_tables"] != [
        "memory_run_transitions",
        "memory_transitions",
    ]:
        raise ValueError("MEM-04 append-only table contract drifted")
    if not valid or not invalid:
        raise ValueError("MEM-04 fixtures cannot be empty")
    case_ids = [item["case_id"] for item in valid + invalid]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("MEM-04 fixture case IDs must be unique")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    validate_assets()
    print("MEM-04 assets verified")
