from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "eval" / "global_recall" / "recall04_semantic_cases_v1.json"


class RecallEvaluationError(RuntimeError):
    pass


def evaluate_predictions(
    cases: dict[str, Any], predictions: dict[str, Any]
) -> dict[str, object]:
    case_values = cases.get("cases")
    prediction_values = predictions.get("predictions")
    if not isinstance(case_values, list) or not case_values:
        raise RecallEvaluationError("semantic cases must be a non-empty array")
    if not isinstance(prediction_values, list):
        raise RecallEvaluationError("predictions must be an array")
    prediction_by_id = {}
    for item in prediction_values:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise RecallEvaluationError("prediction case_id is missing")
        if item["case_id"] in prediction_by_id:
            raise RecallEvaluationError("prediction case_id is duplicated")
        candidates = item.get("candidate_memory_ids")
        if not isinstance(candidates, list) or any(
            not isinstance(value, str) for value in candidates
        ):
            raise RecallEvaluationError("candidate_memory_ids must be strings")
        if len(candidates) > 32 or len(candidates) != len(set(candidates)):
            raise RecallEvaluationError("predictions must contain unique Top32 IDs")
        prediction_by_id[item["case_id"]] = candidates

    rows = []
    recalled = 0
    for case in case_values:
        if not isinstance(case, dict):
            raise RecallEvaluationError("case must be an object")
        case_id = case.get("case_id")
        expected = case.get("expected_memory_ids")
        pool = case.get("memory_pool")
        if (
            not isinstance(case_id, str)
            or not isinstance(expected, list)
            or not expected
            or not isinstance(pool, list)
        ):
            raise RecallEvaluationError("case contract is invalid")
        pool_ids = {
            item.get("memory_id") for item in pool if isinstance(item, dict)
        }
        if len(pool_ids) != len(pool) or not set(expected) <= pool_ids:
            raise RecallEvaluationError("case pool IDs or expected IDs are invalid")
        if case_id not in prediction_by_id:
            raise RecallEvaluationError(f"prediction missing for case: {case_id}")
        predicted = prediction_by_id[case_id]
        if not set(predicted) <= pool_ids:
            raise RecallEvaluationError("prediction contains an ID outside its global pool")
        hit = bool(set(expected) & set(predicted))
        recalled += int(hit)
        rows.append(
            {
                "case_id": case_id,
                "recalled": hit,
                "expected_memory_ids": expected,
                "candidate_memory_ids": predicted,
            }
        )
    if set(prediction_by_id) != {case["case_id"] for case in case_values}:
        raise RecallEvaluationError("predictions contain unknown case IDs")
    return {
        "schema_version": 1,
        "metric": "global_recall_at_32",
        "case_count": len(case_values),
        "recalled_case_count": recalled,
        "global_recall_at_32": recalled / len(case_values),
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate frozen RECALL-04 predictions")
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    print(json.dumps(evaluate_predictions(cases, predictions), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

