from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ._recall_candidates import GlobalRecallTop32, Top32Candidate


QWEN_RERANKER_MODEL_ID = "Qwen/Qwen3-Reranker-0.6B"
RERANK_MAX_PAIR_TOKENS = 128
RERANK_INPUT_SCHEMA_VERSION = "memory-rerank-input-v1"


class RerankerError(RuntimeError):
    pass


class RerankerOutputError(RerankerError):
    pass


@dataclass(frozen=True, slots=True)
class RerankerIdentity:
    model_id: str
    revision: str
    artifact_sha256: str
    deployment_profile: str

    def __post_init__(self) -> None:
        for value, field in (
            (self.model_id, "model_id"),
            (self.revision, "revision"),
            (self.deployment_profile, "deployment_profile"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"reranker {field} must not be empty")
        if len(self.artifact_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.artifact_sha256
        ):
            raise ValueError("reranker artifact_sha256 must be lowercase SHA256")


@dataclass(frozen=True, slots=True)
class RerankScore:
    memory_id: str
    activation_score: float

    def __post_init__(self) -> None:
        if not isinstance(self.memory_id, str) or not self.memory_id.strip():
            raise ValueError("rerank memory_id must not be empty")
        if (
            isinstance(self.activation_score, bool)
            or not isinstance(self.activation_score, (int, float))
            or not math.isfinite(float(self.activation_score))
            or not 0.0 <= float(self.activation_score) <= 1.0
        ):
            raise ValueError("activation_score must be finite in [0, 1]")


@dataclass(frozen=True, slots=True)
class RerankResult:
    identity: RerankerIdentity
    threshold: float
    scores: tuple[RerankScore, ...]
    relative_top_floor: float | None = None
    relative_top_margin: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.threshold, bool)
            or not isinstance(self.threshold, (int, float))
            or not math.isfinite(float(self.threshold))
            or not 0.0 <= float(self.threshold) <= 1.0
        ):
            raise ValueError("reranker threshold must be finite in [0, 1]")
        if not isinstance(self.scores, tuple) or any(
            not isinstance(item, RerankScore) for item in self.scores
        ):
            raise TypeError("reranker scores must be a tuple of RerankScore")
        relative_values = (self.relative_top_floor, self.relative_top_margin)
        if (relative_values[0] is None) != (relative_values[1] is None):
            raise ValueError("relative top selection parameters must be configured together")
        for value in relative_values:
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError("relative top selection parameters must be finite in [0, 1]")

    @property
    def selected_scores(self) -> tuple[RerankScore, ...]:
        ordered = tuple(
            sorted(self.scores, key=lambda item: (-item.activation_score, item.memory_id))
        )
        selected = tuple(
            item for item in self.scores if item.activation_score >= self.threshold
        )
        if selected:
            return tuple(
                sorted(selected, key=lambda item: (-item.activation_score, item.memory_id))
            )
        if self.relative_top_floor is None or not ordered:
            return ()
        top = ordered[0]
        runner_up_score = ordered[1].activation_score if len(ordered) > 1 else 0.0
        if (
            top.activation_score >= self.relative_top_floor
            and top.activation_score - runner_up_score >= self.relative_top_margin
        ):
            return (top,)
        return ()


@runtime_checkable
class MemoryReranker(Protocol):
    @property
    def identity(self) -> RerankerIdentity: ...

    @property
    def activation_threshold(self) -> float: ...

    async def start(self) -> None: ...

    async def rerank(self, batch: GlobalRecallTop32) -> RerankResult: ...

    async def close(self) -> None: ...


def validate_rerank_result(
    batch: GlobalRecallTop32, result: RerankResult
) -> RerankResult:
    if not isinstance(batch, GlobalRecallTop32):
        raise TypeError("batch must be GlobalRecallTop32")
    if not isinstance(result, RerankResult):
        raise RerankerOutputError("reranker returned the wrong result type")
    expected_ids = tuple(candidate.memory_id for candidate in batch.candidates)
    returned_ids = tuple(item.memory_id for item in result.scores)
    if len(returned_ids) != len(set(returned_ids)):
        raise RerankerOutputError("reranker returned duplicate memory IDs")
    if set(returned_ids) != set(expected_ids) or len(returned_ids) != len(expected_ids):
        raise RerankerOutputError(
            "reranker output IDs must match every Top32 candidate exactly"
        )
    if result.identity.model_id == QWEN_RERANKER_MODEL_ID and not result.identity.revision:
        raise RerankerOutputError("production reranker revision must be explicit")
    return result


def render_reranker_pair(batch: GlobalRecallTop32, candidate: Top32Candidate) -> str:
    if candidate.memory_id not in {item.memory_id for item in batch.candidates}:
        raise ValueError("candidate does not belong to the Top32 batch")
    subject = candidate.subject_display_name or candidate.subject_type
    temporal = candidate.temporal_source_text or candidate.temporal_relation
    return "\n".join(
        (
            "<Instruct>: 判断这条女主私有记忆是否有助于理解并自然回应当前亲密对话；yes/no logits only。",
            f"<Query>: {batch.rendered_query}",
            "<Document>: "
            f"memory={candidate.statement}; "
            f"subject={subject}; time={temporal}; "
            f"epistemic={candidate.epistemic_polarity}/{candidate.epistemic_modality}",
        )
    )


class FakeMemoryReranker:
    """Deterministic protocol fake. Scores are supplied by tests, never inferred."""

    def __init__(
        self,
        scores: Mapping[str, float] | None = None,
        *,
        threshold: float = 0.5,
        fail: Exception | None = None,
    ) -> None:
        self._scores = dict(scores or {})
        self._threshold = threshold
        self._fail = fail
        self._identity = RerankerIdentity(
            model_id="test/FakeMemoryReranker",
            revision="fake-v1",
            artifact_sha256=hashlib.sha256(b"fake-memory-reranker-v1").hexdigest(),
            deployment_profile="deterministic-test-double",
        )
        self.calls: list[GlobalRecallTop32] = []
        self.start_calls = 0
        self.close_calls = 0

    @property
    def identity(self) -> RerankerIdentity:
        return self._identity

    @property
    def activation_threshold(self) -> float:
        return self._threshold

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def rerank(self, batch: GlobalRecallTop32) -> RerankResult:
        self.calls.append(batch)
        if self._fail is not None:
            raise self._fail
        return RerankResult(
            identity=self.identity,
            threshold=self._threshold,
            scores=tuple(
                RerankScore(candidate.memory_id, self._scores.get(candidate.memory_id, 0.0))
                for candidate in batch.candidates
            ),
        )
