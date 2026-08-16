from __future__ import annotations

import copy
import json

import pytest

from eval.real_select_e2e.recall_v2 import (
    CASES_PATH,
    RecallV2Error,
    evaluate_predictions,
    run_encoder_predictions,
    validate_case_set,
)
from tests.memory_e2e._helpers import BgeSizedFakeEncoder


def cases() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def test_v2_uses_one_shared_pool_larger_than_top32() -> None:
    value = validate_case_set(cases())
    assert len(value["global_memory_pool"]) == 40
    assert len(value["cases"]) == 8
    assert all("memory_pool" not in item for item in value["cases"])


def test_v2_rejects_the_old_smaller_than_top32_design() -> None:
    value = cases()
    value["global_memory_pool"] = value["global_memory_pool"][:32]
    with pytest.raises(RecallV2Error, match="more than 32"):
        validate_case_set(value)


def test_v2_reports_recall_at_multiple_cutoffs_and_mrr() -> None:
    value = cases()
    pool_ids = [item["memory_id"] for item in value["global_memory_pool"]]
    predictions = []
    for case in value["cases"]:
        target = case["expected_memory_ids"][0]
        ranked = [target, *(item for item in pool_ids if item != target)][:32]
        predictions.append({"case_id": case["case_id"], "candidate_memory_ids": ranked})
    report = evaluate_predictions(
        value,
        {
            "schema_version": 2,
            "dataset_id": value["dataset_id"],
            "predictions": predictions,
        },
    )
    assert report["recall_at_1"] == 1.0
    assert report["global_recall_at_32"] == 1.0
    assert report["mean_reciprocal_rank"] == 1.0


def test_v2_prediction_contract_rejects_unknown_and_duplicate_ids() -> None:
    value = cases()
    predictions = {
        "schema_version": 2,
        "dataset_id": value["dataset_id"],
        "predictions": [
            {"case_id": item["case_id"], "candidate_memory_ids": []}
            for item in value["cases"]
        ],
    }
    predictions["predictions"][0]["candidate_memory_ids"] = ["unknown"]
    with pytest.raises(RecallV2Error, match="unique global Top32"):
        evaluate_predictions(value, predictions)
    duplicate = copy.deepcopy(predictions)
    duplicate["predictions"][0]["candidate_memory_ids"] = [
        value["global_memory_pool"][0]["memory_id"],
        value["global_memory_pool"][0]["memory_id"],
    ]
    with pytest.raises(RecallV2Error, match="unique global Top32"):
        evaluate_predictions(value, duplicate)


def test_real_encoder_runner_emits_exact_top32_without_quality_claim() -> None:
    value = cases()
    predictions = run_encoder_predictions(value, BgeSizedFakeEncoder())
    assert predictions["global_pool_memory_count"] == 40
    assert len(predictions["predictions"]) == 8
    assert all(len(item["candidate_memory_ids"]) == 32 for item in predictions["predictions"])
