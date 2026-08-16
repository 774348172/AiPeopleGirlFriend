from __future__ import annotations

import json
from pathlib import Path

from runtime._memory_materialization import MEMORY_MATERIALIZATION_FAILURE_CODES


ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "eval" / "memory_materialization"


def validate_assets() -> None:
    profile = _load(EVAL_DIR / "mem03_profile_v1.json")
    valid = _load(EVAL_DIR / "fixtures" / "valid" / "materialization_cases_v1.json")
    invalid = _load(EVAL_DIR / "fixtures" / "invalid" / "materialization_cases_v1.json")
    if profile["failure_codes"] != sorted(MEMORY_MATERIALIZATION_FAILURE_CODES):
        raise ValueError("MEM-03 profile failure codes drifted from runtime")
    if not valid or not invalid:
        raise ValueError("MEM-03 fixtures cannot be empty")
    case_ids = [item["case_id"] for item in valid + invalid]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("MEM-03 fixture case IDs must be unique")
    for item in invalid:
        if item["expected_code"] not in MEMORY_MATERIALIZATION_FAILURE_CODES:
            raise ValueError("MEM-03 fixture contains an unknown failure code")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    validate_assets()
    print("MEM-03 assets verified")
