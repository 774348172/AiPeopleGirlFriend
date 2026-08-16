from __future__ import annotations

import os
from pathlib import Path

import pytest

from runtime.benchmark_stage5 import build_scale_database, benchmark_scale_database


def assert_scale_result(result, event_count: int) -> None:
    assert result["event_count"] == event_count
    assert result["fts_virtual_index"] is True
    assert result["conversation_time_index"] is True
    assert result["phrase_query"]["p95_ms"] < 300
    assert result["time_query"]["p95_ms"] < 300
    assert result["normal_context"]["p95_ms"] < 100
    assert result["recall_context"]["p95_ms"] < 300


def test_scale_benchmark_smoke(tmp_path) -> None:
    path = tmp_path / "scale-smoke.sqlite3"
    build_scale_database(path, 2000)
    result = benchmark_scale_database(path, event_count=2000, samples=5)
    assert_scale_result(result, 2000)


@pytest.mark.skipif(
    os.environ.get("AIPEOPLE_RUN_SCALE") != "1",
    reason="set AIPEOPLE_RUN_SCALE=1 to run the 100k-event benchmark",
)
def test_one_hundred_thousand_event_performance(tmp_path) -> None:
    configured = os.environ.get("AIPEOPLE_SCALE_DATABASE")
    path = (
        tmp_path / "scale-100k.sqlite3"
        if configured is None
        else Path(configured)
    )
    if configured is None:
        build_scale_database(path, 100000)
    result = benchmark_scale_database(path, event_count=100000, samples=50)
    assert_scale_result(result, 100000)
