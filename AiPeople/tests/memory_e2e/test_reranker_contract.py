from __future__ import annotations

from dataclasses import replace

import pytest

from runtime._recall_candidates import GlobalRecallTop32, Top32Candidate
from runtime._reranker import (
    FakeMemoryReranker,
    RerankerOutputError,
    RerankResult,
    RerankScore,
    render_reranker_pair,
    validate_rerank_result,
)


def candidate(memory_id: str, rank: int) -> Top32Candidate:
    return Top32Candidate(
        rank=rank,
        memory_id=memory_id,
        memory_version=1,
        coarse_score=0.5,
        memory_kind="shared_experience",
        statement=f"statement for {memory_id}",
        subject_type="both",
        subject_entity_id=None,
        subject_display_name=None,
        temporal_relation="past",
        temporal_source_text="上周",
        temporal_start_at=None,
        temporal_end_at=None,
        temporal_timezone="Asia/Shanghai",
        epistemic_polarity="affirmed",
        epistemic_modality="asserted",
        winning_view_kind="statement",
        winning_view_text=f"statement for {memory_id}",
        source_revision="a" * 64,
    )


def batch() -> GlobalRecallTop32:
    return GlobalRecallTop32(
        query_sha256="1" * 64,
        rendered_query="current conversation",
        query_encoder_revision="bge-r1",
        query_encoder_artifact_sha256="2" * 64,
        generation_id="generation-1",
        global_pool_memory_count=2,
        global_pool_view_count=4,
        candidates=(candidate("memory-a", 1), candidate("memory-b", 2)),
    )


async def test_fake_reranker_scores_every_candidate_and_allows_all_reject() -> None:
    reranker = FakeMemoryReranker(
        {"memory-a": 0.8, "memory-b": 0.4}, threshold=0.9
    )
    result = validate_rerank_result(batch(), await reranker.rerank(batch()))
    assert result.selected_scores == ()
    assert [item.memory_id for item in result.scores] == ["memory-a", "memory-b"]
    rendered = render_reranker_pair(batch(), batch().candidates[0])
    assert "yes/no logits only" in rendered
    assert "memory_id=" not in rendered


def test_production_relative_top_selection_recovers_only_a_clear_leader() -> None:
    identity = FakeMemoryReranker().identity
    recovered = RerankResult(
        identity=identity,
        threshold=0.5,
        scores=(RerankScore("memory-a", 0.26), RerankScore("memory-b", 0.03)),
        relative_top_floor=0.1,
        relative_top_margin=0.05,
    )
    flat = RerankResult(
        identity=identity,
        threshold=0.5,
        scores=(RerankScore("memory-a", 0.20), RerankScore("memory-b", 0.18)),
        relative_top_floor=0.1,
        relative_top_margin=0.05,
    )
    assert [item.memory_id for item in recovered.selected_scores] == ["memory-a"]
    assert flat.selected_scores == ()


def test_relative_top_selection_is_deterministic_and_can_all_reject() -> None:
    result = RerankResult(
        identity=FakeMemoryReranker().identity,
        threshold=0.5,
        scores=(RerankScore("memory-b", 0.08), RerankScore("memory-a", 0.08)),
        relative_top_floor=0.1,
        relative_top_margin=0.05,
    )
    assert result.selected_scores == ()


def test_reranker_output_rejects_missing_unknown_and_duplicate_ids() -> None:
    valid = RerankResult(
        identity=FakeMemoryReranker().identity,
        threshold=0.5,
        scores=(RerankScore("memory-a", 0.9), RerankScore("memory-b", 0.1)),
    )
    validate_rerank_result(batch(), valid)
    with pytest.raises(RerankerOutputError, match="exactly"):
        validate_rerank_result(batch(), replace(valid, scores=valid.scores[:1]))
    with pytest.raises(RerankerOutputError, match="duplicate"):
        validate_rerank_result(
            batch(), replace(valid, scores=(valid.scores[0], valid.scores[0]))
        )


@pytest.mark.parametrize("score", [float("nan"), float("inf"), -0.1, 1.1])
def test_rerank_score_must_be_finite_probability(score: float) -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        RerankScore("memory-a", score)
