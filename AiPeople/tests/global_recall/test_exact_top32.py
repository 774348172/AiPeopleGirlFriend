from __future__ import annotations

from array import array

import pytest

from runtime._global_recall import (
    GLOBAL_RECALL_PROFILE_ID,
    GLOBAL_RECALL_TOP_K,
    ExactGlobalRecall,
    RecallGenerationMismatchError,
    RecallMatrixIntegrityError,
)
from runtime._memory_vectors import (
    BGE_DIMENSION,
    EncoderIdentity,
    ProjectionGeneration,
    VectorMatrixSnapshot,
)
from tests.global_recall._helpers import (
    GENERATION,
    basis,
    make_query,
    make_snapshot,
    make_view,
)


def test_exact_scan_scores_every_view_then_uses_best_view_per_memory() -> None:
    snapshot = make_snapshot(
        (
            (make_view("memory-a", 0), basis(0, 0.2)),
            (make_view("memory-a", 1, kind="evidence"), basis(0, 0.9)),
            (make_view("memory-b", 0), basis(0, 0.7)),
            (make_view("memory-c", 0), basis(0, -0.5)),
        )
    )
    result = ExactGlobalRecall().top32(snapshot, make_query())
    assert result.profile_id == GLOBAL_RECALL_PROFILE_ID
    assert [candidate.memory_id for candidate in result.candidates] == [
        "memory-a",
        "memory-b",
        "memory-c",
    ]
    assert result.candidates[0].winning_view_ordinal == 1
    assert result.candidates[0].winning_view_kind == "evidence"
    assert result.candidates[0].scored_view_count == 2
    assert result.metrics.pool_view_count == result.metrics.scanned_view_count == 4
    assert result.metrics.pool_memory_count == result.metrics.aggregated_memory_count == 3


def test_equal_scores_have_stable_memory_id_and_view_ordinal_tiebreaks() -> None:
    snapshot = make_snapshot(
        (
            (make_view("memory-z", 1), basis(0)),
            (make_view("memory-z", 0), basis(0)),
            (make_view("memory-a", 0), basis(0)),
        )
    )
    first = ExactGlobalRecall().top32(snapshot, make_query())
    second = ExactGlobalRecall().top32(snapshot, make_query())
    assert [item.memory_id for item in first.candidates] == ["memory-a", "memory-z"]
    assert first.candidates[1].winning_view_ordinal == 0
    assert [
        (item.rank, item.memory_id, item.score, item.winning_view_ordinal)
        for item in first.candidates
    ] == [
        (item.rank, item.memory_id, item.score, item.winning_view_ordinal)
        for item in second.candidates
    ]


def test_top32_is_unique_bounded_and_not_a_final_selection_threshold() -> None:
    rows = tuple(
        (make_view(f"memory-{index:02d}", 0), basis(0, index / 40.0))
        for index in range(40)
    )
    result = ExactGlobalRecall().top32(make_snapshot(rows), make_query())
    assert GLOBAL_RECALL_TOP_K == 32
    assert len(result.candidates) == 32
    assert len({item.memory_id for item in result.candidates}) == 32
    assert result.candidates[0].memory_id == "memory-39"
    assert result.candidates[-1].memory_id == "memory-08"
    assert result.metrics.pool_memory_count == 40


def test_empty_generation_and_underfilled_pool_are_explicit() -> None:
    empty = VectorMatrixSnapshot(None, (), array("f"), 0, 0)
    result = ExactGlobalRecall().top32(empty, make_query())
    assert result.generation_id is None
    assert result.candidates == ()
    assert result.metrics.pool_memory_count == 0
    underfilled = ExactGlobalRecall().top32(
        make_snapshot(((make_view("only-memory", 0), basis(0)),)), make_query()
    )
    assert len(underfilled.candidates) == 1


def test_query_identity_and_mid_scan_generation_changes_are_rejected() -> None:
    drift_identity = EncoderIdentity(
        model_id=GENERATION.encoder.model_id,
        revision="drift",
        artifact_sha256="f" * 64,
        dimension=BGE_DIMENSION,
    )
    drift_generation = ProjectionGeneration(
        generation_id="generation-2",
        view_profile_id=GENERATION.view_profile_id,
        encoder=drift_identity,
        created_at=GENERATION.created_at,
        completed_at=GENERATION.completed_at,
    )
    snapshot = make_snapshot(((make_view("memory-a", 0), basis(0)),))
    with pytest.raises(RecallGenerationMismatchError, match="changed during"):
        ExactGlobalRecall().top32(
            snapshot, make_query(), active_generation=lambda: drift_generation
        )

    wrong_query = make_query()
    object.__setattr__(wrong_query, "encoder", drift_identity)
    with pytest.raises(RecallGenerationMismatchError, match="identity"):
        ExactGlobalRecall().top32(snapshot, wrong_query)


def test_corrupt_shape_duplicates_and_mixed_versions_fail_closed() -> None:
    duplicate = make_view("memory-a", 0)
    with pytest.raises(RecallMatrixIntegrityError, match="duplicate"):
        ExactGlobalRecall().top32(
            make_snapshot(((duplicate, basis(0)), (duplicate, basis(1)))), make_query()
        )

    mixed = make_snapshot(
        (
            (make_view("memory-a", 0, version=1), basis(0)),
            (make_view("memory-a", 1, version=2), basis(1)),
        )
    )
    with pytest.raises(RecallMatrixIntegrityError, match="multiple versions"):
        ExactGlobalRecall().top32(mixed, make_query())

    bad_shape = VectorMatrixSnapshot(
        GENERATION,
        (make_view("memory-a", 0),),
        array("f", [1.0]),
        1,
        BGE_DIMENSION,
    )
    with pytest.raises(RecallMatrixIntegrityError, match="value count"):
        ExactGlobalRecall().top32(bad_shape, make_query())


def test_generation_provider_accepts_same_active_generation() -> None:
    snapshot = make_snapshot(((make_view("memory-a", 0), basis(0)),))
    result = ExactGlobalRecall().top32(
        snapshot, make_query(), active_generation=lambda: GENERATION
    )
    assert result.generation_id == GENERATION.generation_id

