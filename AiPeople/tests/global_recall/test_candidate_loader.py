from __future__ import annotations

import hashlib

import pytest

from runtime._global_recall import ExactGlobalRecall
from runtime._memory_vectors import MemoryVectorStore
from runtime._recall_candidates import RecallCandidateStaleError, Top32CandidateLoader
from runtime._selector_query import build_selector_query, encode_selector_query
from tests.memory_vectors._helpers import FakeEncoder, activate, commit_memory, open_ledger
from tests.selector_query._helpers import make_context


class MatchingTokenizer:
    def __init__(self, encoder: FakeEncoder) -> None:
        self._encoder = encoder

    @property
    def identity(self):
        return self._encoder.identity

    def count_tokens(self, text: str) -> int:
        return 64


class BgeSizedFakeEncoder(FakeEncoder):
    def __init__(self) -> None:
        super().__init__(dimension=512)

    def encode(self, texts):
        return [
            [
                float(hashlib.sha256(text.encode("utf-8")).digest()[index % 32] + 1)
                for index in range(self.dimension)
            ]
            for text in texts
        ]


def _query(encoder: FakeEncoder):
    return encode_selector_query(
        build_selector_query(make_context()),
        encoder=encoder,
        tokenizer=MatchingTokenizer(encoder),
        expected_encoder=encoder.identity,
    )


def test_top32_loader_adds_current_memory_metadata_and_preserves_rank(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        memory = activate(store, event)
        encoder = BgeSizedFakeEncoder()
        vectors = MemoryVectorStore(store._connection, encoder)
        vectors.rebuild_all()
        query = _query(encoder)
        recall = ExactGlobalRecall().top32(
            vectors.matrix_snapshot(),
            query,
            active_generation=vectors.active_generation,
        )
        batch = Top32CandidateLoader.from_memory_store(store).load(recall, query)
        assert batch.global_pool_memory_count == 1
        assert batch.global_pool_view_count == recall.metrics.pool_view_count
        assert len(batch.candidates) == 1
        candidate = batch.candidates[0]
        assert candidate.rank == 1
        assert candidate.memory_id == memory.memory_id
        assert candidate.statement == memory.statement
        assert candidate.memory_kind == memory.kind
        assert candidate.subject_type == memory.subject.subject_type
        assert candidate.temporal_relation == memory.temporal.relation
        assert candidate.source_revision == recall.candidates[0].source_revision
    finally:
        ledger.close()


def test_top32_loader_rejects_indexable_memory_version_change_after_scan(tmp_path) -> None:
    ledger = open_ledger(tmp_path)
    try:
        event, store = commit_memory(ledger)
        activate(store, event)
        encoder = BgeSizedFakeEncoder()
        vectors = MemoryVectorStore(store._connection, encoder)
        vectors.rebuild_all()
        query = _query(encoder)
        recall = ExactGlobalRecall().top32(vectors.matrix_snapshot(), query)
        store.decide_memory(
            memory_id="memory-1",
            to_status="disputed",
            reason_code="conflict_detected",
            idempotency_key="dispute-after-recall",
            source_event_id=event.event_id,
        )
        with pytest.raises(RecallCandidateStaleError, match="changed after scan"):
            Top32CandidateLoader.from_memory_store(store).load(recall, query)
    finally:
        ledger.close()
