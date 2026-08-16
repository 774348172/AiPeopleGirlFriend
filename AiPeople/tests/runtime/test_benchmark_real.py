from __future__ import annotations

from runtime.benchmark_real import _p95, _sustained_growth


def test_p95_uses_nearest_rank() -> None:
    assert _p95([float(value) for value in range(1, 21)]) == 19.0
    assert _p95([]) is None


def test_sustained_growth_ignores_small_drift() -> None:
    assert _sustained_growth([100, 102, 101, 104, 105, 106, 107, 108], 64) is False
    assert _sustained_growth([100, 101, 102, 103, 200, 201, 202, 203], 64) is True
