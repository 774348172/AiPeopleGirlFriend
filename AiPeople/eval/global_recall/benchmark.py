from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import struct
import time
from array import array
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from runtime._global_recall import ExactGlobalRecall
from runtime._memory_vectors import (
    BGE_DIMENSION,
    BGE_MODEL_ID,
    EncoderIdentity,
    ProjectionGeneration,
    SelectorView,
    VectorMatrixSnapshot,
)
from runtime._selector_query import (
    SELECTOR_QUERY_SCHEMA_VERSION,
    SelectorQuery,
    SelectorQueryEncoding,
    SelectorTimeContext,
    SelectorWorkingState,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = (
    ROOT / "eval" / "global_recall" / "recall04_engineering_benchmark_v1.json"
)
BENCHMARK_IDENTITY = EncoderIdentity(
    model_id=BGE_MODEL_ID,
    revision="recall04-synthetic-benchmark",
    artifact_sha256=hashlib.sha256(b"recall04-synthetic-benchmark").hexdigest(),
    dimension=BGE_DIMENSION,
)
BENCHMARK_GENERATION = ProjectionGeneration(
    generation_id="recall04-synthetic-generation",
    view_profile_id="mem05-selector-view-v1",
    encoder=BENCHMARK_IDENTITY,
    created_at="2026-08-08T00:00:00Z",
    completed_at="2026-08-08T00:00:01Z",
)


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    source_event_count: int = 100_000
    active_memory_count: int = 10_000
    views_per_memory: int = 3
    warmup_samples: int = 3
    measured_samples: int = 30
    concurrent_workers: int = 4
    concurrent_samples_per_worker: int = 5
    scan_top32_p95_ms_max: float = 100.0
    concurrent_scan_top32_p95_ms_max: float = 150.0


def build_synthetic_snapshot(config: BenchmarkConfig) -> VectorMatrixSnapshot:
    rows = config.active_memory_count * config.views_per_memory
    matrix = np.zeros((rows, BGE_DIMENSION), dtype=np.float32)
    views = []
    row = 0
    for memory_index in range(config.active_memory_count):
        base_score = (memory_index + 1) / (config.active_memory_count + 1)
        for ordinal in range(config.views_per_memory):
            score = min(0.999999, base_score + ordinal * 0.000001)
            matrix[row, 0] = score
            matrix[row, 1] = math.sqrt(1.0 - score * score)
            memory_id = f"memory-{memory_index:08d}"
            text = f"synthetic selector view {memory_index}:{ordinal}"
            views.append(
                SelectorView(
                    memory_id=memory_id,
                    memory_version=1,
                    view_ordinal=ordinal,
                    view_kind=("statement" if ordinal == 0 else "evidence"),
                    text=text,
                    text_sha256=f"{row:064x}"[-64:],
                    source_revision=f"{memory_index:064x}"[-64:],
                )
            )
            row += 1
    values = array("f")
    values.frombytes(matrix.tobytes(order="C"))
    return VectorMatrixSnapshot(
        generation=BENCHMARK_GENERATION,
        views=tuple(views),
        values=values,
        rows=rows,
        dimension=BGE_DIMENSION,
    )


def run_benchmark(config: BenchmarkConfig = BenchmarkConfig()) -> dict[str, object]:
    snapshot = build_synthetic_snapshot(config)
    scanner = ExactGlobalRecall()
    for sample in range(config.warmup_samples):
        scanner.top32(snapshot, _benchmark_query(-sample - 1))

    serial = [
        scanner.top32(snapshot, _benchmark_query(sample)).metrics.total_ms
        for sample in range(config.measured_samples)
    ]

    def run_worker(worker: int) -> list[float]:
        worker_scanner = ExactGlobalRecall()
        return [
            worker_scanner.top32(
                snapshot,
                _benchmark_query(10_000 + worker * 100 + sample),
            ).metrics.total_ms
            for sample in range(config.concurrent_samples_per_worker)
        ]

    concurrent_started = time.perf_counter_ns()
    with ThreadPoolExecutor(max_workers=config.concurrent_workers) as executor:
        concurrent_groups = list(executor.map(run_worker, range(config.concurrent_workers)))
    concurrent_wall_ms = (time.perf_counter_ns() - concurrent_started) / 1_000_000.0
    concurrent = [value for group in concurrent_groups for value in group]
    serial_p95 = _percentile(serial, 0.95)
    concurrent_p95 = _percentile(concurrent, 0.95)
    return {
        "schema_version": 1,
        "checkpoint": "RECALL-04",
        "status": (
            "passed"
            if serial_p95 < config.scan_top32_p95_ms_max
            and concurrent_p95 < config.concurrent_scan_top32_p95_ms_max
            else "failed"
        ),
        "measured_at": datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "processor": platform.processor(),
        },
        "config": asdict(config),
        "pool": {
            "source_event_count": config.source_event_count,
            "active_memory_count": config.active_memory_count,
            "selector_view_count": snapshot.rows,
            "dimension": snapshot.dimension,
            "matrix_bytes": len(snapshot.values) * snapshot.values.itemsize,
        },
        "serial_ms": _summary(serial),
        "concurrent_ms": {
            **_summary(concurrent),
            "workers": config.concurrent_workers,
            "wall_ms": concurrent_wall_ms,
        },
        "scope": {
            "includes": "hot exact matrix dot-product, memory_id aggregation, stable Top32 and concurrent read-only scans",
            "excludes": [
                "BGE query encoding",
                "SQLite projection rebuild",
                "Qwen reranker",
                "semantic recall quality",
            ],
        },
    }


def _summary(samples: list[float]) -> dict[str, float | int]:
    return {
        "samples": len(samples),
        "min": min(samples),
        "p50": statistics.median(samples),
        "p95": _percentile(samples, 0.95),
        "max": max(samples),
        "mean": statistics.fmean(samples),
    }


def _benchmark_query(seed: int) -> SelectorQueryEncoding:
    angle = (seed % 97) / 97.0 * (math.pi / 2.0)
    vector = (math.cos(angle), math.sin(angle)) + (0.0,) * (BGE_DIMENSION - 2)
    blob = struct.pack(f"<{BGE_DIMENSION}f", *vector)
    rendered = f"recall04 synthetic changing query {seed}"
    query = SelectorQuery(
        schema_version=SELECTOR_QUERY_SCHEMA_VERSION,
        current_user_message=rendered,
        recent_dialogue=(),
        compact_working_state=SelectorWorkingState(False, 0),
        current_time_context=SelectorTimeContext(
            "2026-08-08T12:00:00+08:00", "Asia/Shanghai", 6, "afternoon"
        ),
    )
    return SelectorQueryEncoding(
        query=query,
        query_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        rendered_text=rendered,
        rendered_text_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        encoder=BENCHMARK_IDENTITY,
        token_count=8,
        vector=vector,
        vector_blob=blob,
        vector_sha256=hashlib.sha256(blob).hexdigest(),
    )


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run RECALL-04 exact recall benchmark")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--active-memories", type=int, default=10_000)
    parser.add_argument("--views-per-memory", type=int, default=3)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    report = run_benchmark(
        BenchmarkConfig(
            active_memory_count=args.active_memories,
            views_per_memory=args.views_per_memory,
            measured_samples=args.samples,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
