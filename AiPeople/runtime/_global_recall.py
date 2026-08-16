from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import numpy as np

from ._memory_vectors import ProjectionGeneration, SelectorView, VectorMatrixSnapshot
from ._selector_query import SelectorQueryEncoding


GLOBAL_RECALL_TOP_K: Final = 32
GLOBAL_RECALL_PROFILE_ID: Final = "recall02-04-exact-top32-v1"


class GlobalRecallError(RuntimeError):
    pass


class RecallGenerationMismatchError(GlobalRecallError):
    pass


class RecallMatrixIntegrityError(GlobalRecallError):
    pass


@dataclass(frozen=True, slots=True)
class GlobalRecallCandidate:
    rank: int
    memory_id: str
    memory_version: int
    score: float
    winning_view_ordinal: int
    winning_view_kind: str
    winning_view_text: str
    winning_view_text_sha256: str
    source_revision: str
    scored_view_count: int

    def __post_init__(self) -> None:
        if self.rank <= 0:
            raise ValueError("candidate rank must be positive")
        if not self.memory_id.strip():
            raise ValueError("candidate memory_id must not be empty")
        if self.memory_version <= 0:
            raise ValueError("candidate memory_version must be positive")
        if not math.isfinite(self.score):
            raise ValueError("candidate score must be finite")
        if self.winning_view_ordinal < 0:
            raise ValueError("winning_view_ordinal must be non-negative")
        if self.scored_view_count <= 0:
            raise ValueError("scored_view_count must be positive")


@dataclass(frozen=True, slots=True)
class GlobalRecallMetrics:
    pool_memory_count: int
    pool_view_count: int
    scanned_view_count: int
    aggregated_memory_count: int
    returned_candidate_count: int
    dot_product_ms: float
    aggregate_and_topk_ms: float
    total_ms: float

    def __post_init__(self) -> None:
        counts = (
            self.pool_memory_count,
            self.pool_view_count,
            self.scanned_view_count,
            self.aggregated_memory_count,
            self.returned_candidate_count,
        )
        if any(not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("global recall counts must be non-negative integers")
        timings = (self.dot_product_ms, self.aggregate_and_topk_ms, self.total_ms)
        if any(not math.isfinite(value) or value < 0.0 for value in timings):
            raise ValueError("global recall timings must be finite and non-negative")
        if self.scanned_view_count != self.pool_view_count:
            raise ValueError("every pool view must be scanned")
        if self.aggregated_memory_count != self.pool_memory_count:
            raise ValueError("every pool memory must survive aggregation")
        if self.returned_candidate_count > min(
            GLOBAL_RECALL_TOP_K, self.pool_memory_count
        ):
            raise ValueError("returned candidate count exceeds Top32")


@dataclass(frozen=True, slots=True)
class GlobalRecallResult:
    profile_id: str
    query_sha256: str
    query_vector_sha256: str
    generation_id: str | None
    candidates: tuple[GlobalRecallCandidate, ...]
    metrics: GlobalRecallMetrics

    def __post_init__(self) -> None:
        if self.profile_id != GLOBAL_RECALL_PROFILE_ID:
            raise ValueError("unexpected global recall profile_id")
        if len(self.candidates) != self.metrics.returned_candidate_count:
            raise ValueError("candidate count differs from metrics")
        if tuple(candidate.rank for candidate in self.candidates) != tuple(
            range(1, len(self.candidates) + 1)
        ):
            raise ValueError("candidate ranks must be contiguous")
        if len({candidate.memory_id for candidate in self.candidates}) != len(
            self.candidates
        ):
            raise ValueError("Top32 candidates must be unique by memory_id")


@dataclass(slots=True)
class _MemoryWinner:
    view: SelectorView
    score: float
    scored_view_count: int


class ExactGlobalRecall:
    """Stateless exact scanner over one immutable vector generation snapshot."""

    def __init__(self, *, clock_ns: Callable[[], int] = time.perf_counter_ns) -> None:
        self._clock_ns = clock_ns

    def top32(
        self,
        snapshot: VectorMatrixSnapshot,
        query: SelectorQueryEncoding,
        *,
        active_generation: Callable[[], ProjectionGeneration | None] | None = None,
    ) -> GlobalRecallResult:
        _validate_snapshot(snapshot, query)
        started = self._clock_ns()
        generation = snapshot.generation
        if snapshot.rows == 0:
            dot_finished = self._clock_ns()
            aggregate_finished = self._clock_ns()
            _verify_generation_unchanged(generation, active_generation)
            return _result(
                query=query,
                generation=generation,
                candidates=(),
                pool_memory_count=0,
                pool_view_count=0,
                started=started,
                dot_finished=dot_finished,
                aggregate_finished=aggregate_finished,
            )

        matrix = np.frombuffer(snapshot.values, dtype=np.float32).reshape(
            snapshot.rows, snapshot.dimension
        )
        query_vector = np.asarray(query.vector, dtype=np.float32)
        scores = matrix @ query_vector
        dot_finished = self._clock_ns()
        if scores.shape != (snapshot.rows,) or not np.isfinite(scores).all():
            raise RecallMatrixIntegrityError("exact scan returned invalid scores")

        winners: dict[str, _MemoryWinner] = {}
        versions: dict[str, int] = {}
        for view, raw_score in zip(snapshot.views, scores, strict=True):
            score = float(raw_score)
            previous_version = versions.setdefault(view.memory_id, view.memory_version)
            if previous_version != view.memory_version:
                raise RecallMatrixIntegrityError(
                    "one memory_id has multiple versions in the active snapshot"
                )
            winner = winners.get(view.memory_id)
            if winner is None:
                winners[view.memory_id] = _MemoryWinner(view, score, 1)
                continue
            winner.scored_view_count += 1
            if score > winner.score or (
                score == winner.score and view.view_ordinal < winner.view.view_ordinal
            ):
                winner.view = view
                winner.score = score

        ordered = sorted(
            winners.items(),
            key=lambda item: (
                -item[1].score,
                item[0],
                item[1].view.view_ordinal,
            ),
        )[:GLOBAL_RECALL_TOP_K]
        candidates = tuple(
            GlobalRecallCandidate(
                rank=rank,
                memory_id=memory_id,
                memory_version=winner.view.memory_version,
                score=winner.score,
                winning_view_ordinal=winner.view.view_ordinal,
                winning_view_kind=winner.view.view_kind,
                winning_view_text=winner.view.text,
                winning_view_text_sha256=winner.view.text_sha256,
                source_revision=winner.view.source_revision,
                scored_view_count=winner.scored_view_count,
            )
            for rank, (memory_id, winner) in enumerate(ordered, start=1)
        )
        aggregate_finished = self._clock_ns()
        _verify_generation_unchanged(generation, active_generation)
        return _result(
            query=query,
            generation=generation,
            candidates=candidates,
            pool_memory_count=len(winners),
            pool_view_count=snapshot.rows,
            started=started,
            dot_finished=dot_finished,
            aggregate_finished=aggregate_finished,
        )


def _validate_snapshot(
    snapshot: VectorMatrixSnapshot, query: SelectorQueryEncoding
) -> None:
    if not isinstance(snapshot, VectorMatrixSnapshot):
        raise TypeError("snapshot must be a VectorMatrixSnapshot")
    if not isinstance(query, SelectorQueryEncoding):
        raise TypeError("query must be a SelectorQueryEncoding")
    if snapshot.rows < 0 or snapshot.dimension < 0:
        raise RecallMatrixIntegrityError("snapshot dimensions must be non-negative")
    if len(snapshot.views) != snapshot.rows:
        raise RecallMatrixIntegrityError("snapshot view count differs from row count")
    if len(snapshot.values) != snapshot.rows * snapshot.dimension:
        raise RecallMatrixIntegrityError("snapshot value count differs from matrix shape")
    keys = [(view.memory_id, view.view_ordinal) for view in snapshot.views]
    if len(keys) != len(set(keys)):
        raise RecallMatrixIntegrityError("snapshot contains duplicate selector views")
    if snapshot.generation is None:
        if snapshot.rows != 0 or snapshot.dimension != 0:
            raise RecallMatrixIntegrityError(
                "snapshot without a generation must be an empty 0x0 matrix"
            )
        return
    if snapshot.generation.encoder != query.encoder:
        raise RecallGenerationMismatchError(
            "query encoder identity differs from vector generation"
        )
    if snapshot.dimension != snapshot.generation.encoder.dimension:
        raise RecallMatrixIntegrityError("snapshot dimension differs from generation")
    if len(query.vector) != snapshot.dimension:
        raise RecallMatrixIntegrityError("query dimension differs from matrix")


def _verify_generation_unchanged(
    expected: ProjectionGeneration | None,
    provider: Callable[[], ProjectionGeneration | None] | None,
) -> None:
    if provider is None:
        return
    current = provider()
    expected_id = expected.generation_id if expected is not None else None
    current_id = current.generation_id if current is not None else None
    if current_id != expected_id:
        raise RecallGenerationMismatchError(
            "active vector generation changed during exact recall"
        )


def _result(
    *,
    query: SelectorQueryEncoding,
    generation: ProjectionGeneration | None,
    candidates: tuple[GlobalRecallCandidate, ...],
    pool_memory_count: int,
    pool_view_count: int,
    started: int,
    dot_finished: int,
    aggregate_finished: int,
) -> GlobalRecallResult:
    dot_ms = (dot_finished - started) / 1_000_000.0
    aggregate_ms = (aggregate_finished - dot_finished) / 1_000_000.0
    total_ms = (aggregate_finished - started) / 1_000_000.0
    return GlobalRecallResult(
        profile_id=GLOBAL_RECALL_PROFILE_ID,
        query_sha256=query.query_sha256,
        query_vector_sha256=query.vector_sha256,
        generation_id=(generation.generation_id if generation is not None else None),
        candidates=candidates,
        metrics=GlobalRecallMetrics(
            pool_memory_count=pool_memory_count,
            pool_view_count=pool_view_count,
            scanned_view_count=pool_view_count,
            aggregated_memory_count=pool_memory_count,
            returned_candidate_count=len(candidates),
            dot_product_ms=dot_ms,
            aggregate_and_topk_ms=aggregate_ms,
            total_ms=total_ms,
        ),
    )
