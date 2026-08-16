from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Literal

from ._context import ReplyContext
from ._global_recall import ExactGlobalRecall
from ._memory_vectors import EmbeddingEncoder, MemoryVectorStore
from ._recall_candidates import GlobalRecallTop32, Top32CandidateLoader
from ._reranker import MemoryReranker, RerankScore, validate_rerank_result
from ._selector_query import SelectorTokenizer, build_selector_query, encode_selector_query


SelectionStatus = Literal["complete", "empty_pool", "projection_unavailable"]


@dataclass(frozen=True, slots=True)
class MemorySelectionMetrics:
    query_encode_ms: float
    global_scan_ms: float
    top32_load_ms: float
    rerank_ms: float
    total_ms: float
    pool_memory_count: int
    pool_view_count: int
    candidate_count: int
    selected_count: int

    def __post_init__(self) -> None:
        timings = (
            self.query_encode_ms,
            self.global_scan_ms,
            self.top32_load_ms,
            self.rerank_ms,
            self.total_ms,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in timings):
            raise ValueError("memory selection timings must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class MemorySelectionOutcome:
    status: SelectionStatus
    batch: GlobalRecallTop32 | None
    selected_scores: tuple[RerankScore, ...]
    selector_version: str
    metrics: MemorySelectionMetrics


class MemorySelectionPipeline:
    def __init__(
        self,
        *,
        vector_store: MemoryVectorStore,
        query_encoder: EmbeddingEncoder,
        query_tokenizer: SelectorTokenizer,
        reranker: MemoryReranker,
        rerank_timeout_seconds: float = 0.25,
    ) -> None:
        if rerank_timeout_seconds <= 0.0:
            raise ValueError("rerank_timeout_seconds must be positive")
        self._vector_store = vector_store
        self._query_encoder = query_encoder
        self._query_tokenizer = query_tokenizer
        self._reranker = reranker
        self._rerank_timeout_seconds = rerank_timeout_seconds
        self._scanner = ExactGlobalRecall()
        self._candidate_loader = Top32CandidateLoader(vector_store._connection)

    async def start(self) -> None:
        await self._reranker.start()

    async def close(self) -> None:
        await self._reranker.close()

    async def select(self, context: ReplyContext) -> MemorySelectionOutcome:
        started = time.perf_counter()
        generation = self._vector_store.active_generation()
        selector_version = self._selector_version()
        if generation is None:
            return MemorySelectionOutcome(
                status="projection_unavailable",
                batch=None,
                selected_scores=(),
                selector_version=selector_version,
                metrics=_metrics(total_ms=_elapsed_ms(started)),
            )

        query_started = time.perf_counter()
        query = encode_selector_query(
            build_selector_query(context),
            encoder=self._query_encoder,
            tokenizer=self._query_tokenizer,
            expected_encoder=generation.encoder,
        )
        query_ms = _elapsed_ms(query_started)
        scan_started = time.perf_counter()
        recall = self._scanner.top32(
            self._vector_store.matrix_snapshot(),
            query,
            active_generation=self._vector_store.active_generation,
        )
        scan_ms = _elapsed_ms(scan_started)
        load_started = time.perf_counter()
        batch = self._candidate_loader.load(recall, query)
        load_ms = _elapsed_ms(load_started)
        if not batch.candidates:
            return MemorySelectionOutcome(
                status="empty_pool",
                batch=batch,
                selected_scores=(),
                selector_version=selector_version,
                metrics=_metrics(
                    query_ms=query_ms,
                    scan_ms=scan_ms,
                    load_ms=load_ms,
                    total_ms=_elapsed_ms(started),
                ),
            )

        rerank_started = time.perf_counter()
        result = await asyncio.wait_for(
            self._reranker.rerank(batch), timeout=self._rerank_timeout_seconds
        )
        validated = validate_rerank_result(batch, result)
        if (
            validated.identity != self._reranker.identity
            or validated.threshold != self._reranker.activation_threshold
        ):
            raise RuntimeError(
                "reranker result identity or threshold differs from configured backend"
            )
        rerank_ms = _elapsed_ms(rerank_started)
        selected = validated.selected_scores
        return MemorySelectionOutcome(
            status="complete",
            batch=batch,
            selected_scores=selected,
            selector_version=selector_version,
            metrics=MemorySelectionMetrics(
                query_encode_ms=query_ms,
                global_scan_ms=scan_ms,
                top32_load_ms=load_ms,
                rerank_ms=rerank_ms,
                total_ms=_elapsed_ms(started),
                pool_memory_count=batch.global_pool_memory_count,
                pool_view_count=batch.global_pool_view_count,
                candidate_count=len(batch.candidates),
                selected_count=len(selected),
            ),
        )

    def _selector_version(self) -> str:
        identity = self._reranker.identity
        return (
            "global-exact-top32-v1+"
            f"{identity.model_id}@{identity.revision}:"
            f"{identity.artifact_sha256[:12]}"
        )


def _metrics(
    *,
    query_ms: float = 0.0,
    scan_ms: float = 0.0,
    load_ms: float = 0.0,
    rerank_ms: float = 0.0,
    total_ms: float,
) -> MemorySelectionMetrics:
    return MemorySelectionMetrics(
        query_encode_ms=query_ms,
        global_scan_ms=scan_ms,
        top32_load_ms=load_ms,
        rerank_ms=rerank_ms,
        total_ms=total_ms,
        pool_memory_count=0,
        pool_view_count=0,
        candidate_count=0,
        selected_count=0,
    )


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0
