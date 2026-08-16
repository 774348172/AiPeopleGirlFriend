from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from eval.global_recall.benchmark import BenchmarkConfig, run_benchmark
from eval.global_recall.evaluate import RecallEvaluationError, evaluate_predictions
from runtime._global_recall import ExactGlobalRecall
from tests.global_recall._helpers import basis, make_query, make_snapshot, make_view


def test_same_snapshot_supports_concurrent_read_only_scans() -> None:
    snapshot = make_snapshot(
        tuple(
            (make_view(f"memory-{index:03d}", 0), basis(0, index / 100.0))
            for index in range(100)
        )
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: ExactGlobalRecall().top32(snapshot, make_query()), range(16)
            )
        )
    expected = [candidate.memory_id for candidate in results[0].candidates]
    assert all([candidate.memory_id for candidate in item.candidates] == expected for item in results)
    assert all(item.metrics.scanned_view_count == 100 for item in results)


def test_small_benchmark_reports_full_pool_and_latency_gate() -> None:
    report = run_benchmark(
        BenchmarkConfig(
            source_event_count=1_000,
            active_memory_count=100,
            views_per_memory=2,
            warmup_samples=1,
            measured_samples=3,
            concurrent_workers=2,
            concurrent_samples_per_worker=2,
            scan_top32_p95_ms_max=5_000.0,
        )
    )
    assert report["status"] == "passed"
    assert report["pool"]["active_memory_count"] == 100
    assert report["pool"]["selector_view_count"] == 200
    assert report["serial_ms"]["samples"] == 3


def test_global_recall_at_32_evaluator_requires_complete_unique_global_ids(tmp_path) -> None:
    cases = {
        "cases": [
            {
                "case_id": "case-1",
                "expected_memory_ids": ["memory-target"],
                "memory_pool": [
                    {"memory_id": "memory-target", "statement": "目标"},
                    {"memory_id": "memory-negative", "statement": "负例"},
                ],
            },
            {
                "case_id": "case-2",
                "expected_memory_ids": ["memory-other"],
                "memory_pool": [
                    {"memory_id": "memory-other", "statement": "另一个目标"}
                ],
            },
        ]
    }
    predictions = {
        "predictions": [
            {"case_id": "case-1", "candidate_memory_ids": ["memory-target"]},
            {"case_id": "case-2", "candidate_memory_ids": []},
        ]
    }
    report = evaluate_predictions(cases, predictions)
    assert report["global_recall_at_32"] == 0.5

    predictions["predictions"][0]["candidate_memory_ids"] = ["unknown"]
    with pytest.raises(RecallEvaluationError, match="outside"):
        evaluate_predictions(cases, predictions)


def test_frozen_semantic_cases_have_unique_ids_targets_and_hard_negatives() -> None:
    path = Path("eval/global_recall/recall04_semantic_cases_v1.json")
    cases = json.loads(path.read_text(encoding="utf-8"))
    values = cases["cases"]
    assert len(values) >= 8
    assert len({case["case_id"] for case in values}) == len(values)
    for case in values:
        pool_ids = [item["memory_id"] for item in case["memory_pool"]]
        assert len(pool_ids) == len(set(pool_ids)) >= 4
        assert set(case["expected_memory_ids"]) <= set(pool_ids)
        assert any(item["label"] == "hard_negative" for item in case["memory_pool"])
