from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from runtime._memory_vectors import EmbeddingEncoder
from runtime._selector_query import (
    SELECTOR_QUERY_SCHEMA_VERSION,
    SelectorDialogueTurn,
    SelectorQuery,
    SelectorTimeContext,
    SelectorWorkingState,
    render_selector_query_text,
)


ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = ROOT / "eval" / "real_select_e2e" / "recall04_semantic_cases_v2.json"
TOP_KS = (1, 5, 10, 32)


class RecallV2Error(RuntimeError):
    pass


def validate_case_set(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != 2:
        raise RecallV2Error("RECALL-04 v2 schema_version must equal 2")
    pool = value.get("global_memory_pool")
    cases = value.get("cases")
    if not isinstance(pool, list) or len(pool) <= 32:
        raise RecallV2Error("global memory pool must contain more than 32 memories")
    if not isinstance(cases, list) or not cases:
        raise RecallV2Error("RECALL-04 v2 cases must be a non-empty array")
    pool_ids: set[str] = set()
    for item in pool:
        if not isinstance(item, dict):
            raise RecallV2Error("global memory pool entries must be objects")
        memory_id = item.get("memory_id")
        statement = item.get("statement")
        if not isinstance(memory_id, str) or not memory_id or memory_id in pool_ids:
            raise RecallV2Error("global memory pool IDs must be unique non-empty strings")
        if not isinstance(statement, str) or not statement.strip():
            raise RecallV2Error("global memory statements must be non-empty strings")
        pool_ids.add(memory_id)
    case_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise RecallV2Error("RECALL-04 v2 cases must be objects")
        case_id = case.get("case_id")
        expected = case.get("expected_memory_ids")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise RecallV2Error("case IDs must be unique non-empty strings")
        if not isinstance(expected, list) or not expected or not set(expected) <= pool_ids:
            raise RecallV2Error("expected memory IDs must exist in the global pool")
        _selector_query(case.get("selector_query"))
        case_ids.add(case_id)
    return value


def run_encoder_predictions(
    cases: dict[str, Any], encoder: EmbeddingEncoder
) -> dict[str, Any]:
    validate_case_set(cases)
    pool = cases["global_memory_pool"]
    statements = [item["statement"] for item in pool]
    memory_ids = [item["memory_id"] for item in pool]
    memory_vectors = tuple(_normalized(row) for row in encoder.encode(statements))
    if len(memory_vectors) != len(pool):
        raise RecallV2Error("BGE encoder returned the wrong memory vector count")
    predictions = []
    for case in cases["cases"]:
        query = _selector_query(case["selector_query"])
        encoded = encoder.encode((render_selector_query_text(query),))
        if len(encoded) != 1:
            raise RecallV2Error("BGE encoder must return exactly one query vector")
        query_vector = _normalized(encoded[0])
        ranked = sorted(
            (
                (math.fsum(a * b for a, b in zip(query_vector, vector, strict=True)), memory_id)
                for memory_id, vector in zip(memory_ids, memory_vectors, strict=True)
            ),
            key=lambda item: (-item[0], item[1]),
        )[:32]
        predictions.append(
            {
                "case_id": case["case_id"],
                "candidate_memory_ids": [memory_id for _, memory_id in ranked],
            }
        )
    return {
        "schema_version": 2,
        "dataset_id": cases["dataset_id"],
        "encoder": {
            "model_id": encoder.identity.model_id,
            "revision": encoder.identity.revision,
            "artifact_sha256": encoder.identity.artifact_sha256,
        },
        "global_pool_memory_count": len(pool),
        "predictions": predictions,
    }


def evaluate_predictions(
    cases: dict[str, Any], predictions: dict[str, Any]
) -> dict[str, Any]:
    validate_case_set(cases)
    if predictions.get("schema_version") != 2:
        raise RecallV2Error("prediction schema_version must equal 2")
    if predictions.get("dataset_id") != cases.get("dataset_id"):
        raise RecallV2Error("prediction dataset_id does not match the case set")
    pool_ids = {item["memory_id"] for item in cases["global_memory_pool"]}
    values = predictions.get("predictions")
    if not isinstance(values, list):
        raise RecallV2Error("predictions must be an array")
    by_id: dict[str, list[str]] = {}
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise RecallV2Error("prediction case_id is missing")
        case_id = item["case_id"]
        candidates = item.get("candidate_memory_ids")
        if case_id in by_id:
            raise RecallV2Error("prediction case_id is duplicated")
        if (
            not isinstance(candidates, list)
            or any(not isinstance(value, str) for value in candidates)
            or len(candidates) > 32
            or len(candidates) != len(set(candidates))
            or not set(candidates) <= pool_ids
        ):
            raise RecallV2Error("prediction candidates must be unique global Top32 IDs")
        by_id[case_id] = candidates
    expected_case_ids = {case["case_id"] for case in cases["cases"]}
    if set(by_id) != expected_case_ids:
        raise RecallV2Error("predictions must cover every case exactly once")
    hits = {value: 0 for value in TOP_KS}
    reciprocal_rank = 0.0
    rows = []
    for case in cases["cases"]:
        ranked = by_id[case["case_id"]]
        expected = set(case["expected_memory_ids"])
        first_rank = next(
            (index for index, memory_id in enumerate(ranked, start=1) if memory_id in expected),
            None,
        )
        for top_k in TOP_KS:
            hits[top_k] += int(bool(expected & set(ranked[:top_k])))
        if first_rank is not None:
            reciprocal_rank += 1.0 / first_rank
        rows.append(
            {
                "case_id": case["case_id"],
                "first_expected_rank": first_rank,
                "candidate_memory_ids": ranked,
            }
        )
    count = len(cases["cases"])
    return {
        "schema_version": 2,
        "metric_family": "global_semantic_recall",
        "dataset_id": cases["dataset_id"],
        "case_count": count,
        "global_pool_memory_count": len(pool_ids),
        "recall_at_1": hits[1] / count,
        "recall_at_5": hits[5] / count,
        "recall_at_10": hits[10] / count,
        "global_recall_at_32": hits[32] / count,
        "mean_reciprocal_rank": reciprocal_rank / count,
        "results": rows,
    }


def _selector_query(value: Any) -> SelectorQuery:
    if not isinstance(value, dict):
        raise RecallV2Error("selector_query must be an object")
    try:
        state = value["compact_working_state"]
        time = value["current_time_context"]
        return SelectorQuery(
            schema_version=value["schema_version"],
            current_user_message=value["current_user_message"],
            recent_dialogue=tuple(
                SelectorDialogueTurn(role=item["role"], text=item["text"])
                for item in value["recent_dialogue"]
            ),
            compact_working_state=SelectorWorkingState(
                has_completed_exchange=state["has_completed_exchange"],
                unresolved_prior_user_turns=state["unresolved_prior_user_turns"],
            ),
            current_time_context=SelectorTimeContext(
                local_datetime=time["local_datetime"],
                timezone=time["timezone"],
                weekday=time["weekday"],
                part_of_day=time["part_of_day"],
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RecallV2Error("selector_query does not match the RECALL-01 contract") from error


def _normalized(row) -> tuple[float, ...]:
    values = tuple(float(value) for value in row)
    if not values or any(not math.isfinite(value) for value in values):
        raise RecallV2Error("BGE encoder returned an invalid vector")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise RecallV2Error("BGE encoder returned a zero vector")
    return tuple(value / norm for value in values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate RECALL-04 v2 predictions")
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    print(json.dumps(evaluate_predictions(cases, predictions), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
