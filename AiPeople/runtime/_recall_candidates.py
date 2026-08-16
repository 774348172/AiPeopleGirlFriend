from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from ._global_recall import GLOBAL_RECALL_TOP_K, GlobalRecallResult
from ._memory_contracts import (
    MEMORY_INDEXABLE_STATUSES,
    canonical_memory_json,
    memory_representation_from_json,
)
from ._selector_query import SelectorQueryEncoding


class RecallCandidateError(RuntimeError):
    pass


class RecallCandidateStaleError(RecallCandidateError):
    pass


@dataclass(frozen=True, slots=True)
class Top32Candidate:
    rank: int
    memory_id: str
    memory_version: int
    coarse_score: float
    memory_kind: str
    statement: str
    subject_type: str
    subject_entity_id: str | None
    subject_display_name: str | None
    temporal_relation: str
    temporal_source_text: str | None
    temporal_start_at: str | None
    temporal_end_at: str | None
    temporal_timezone: str | None
    epistemic_polarity: str
    epistemic_modality: str
    winning_view_kind: str
    winning_view_text: str
    source_revision: str


@dataclass(frozen=True, slots=True)
class GlobalRecallTop32:
    query_sha256: str
    rendered_query: str
    query_encoder_revision: str
    query_encoder_artifact_sha256: str
    generation_id: str | None
    global_pool_memory_count: int
    global_pool_view_count: int
    candidates: tuple[Top32Candidate, ...]

    def __post_init__(self) -> None:
        if len(self.candidates) > GLOBAL_RECALL_TOP_K:
            raise ValueError("GlobalRecallTop32 cannot exceed 32 candidates")
        if tuple(item.rank for item in self.candidates) != tuple(
            range(1, len(self.candidates) + 1)
        ):
            raise ValueError("Top32 ranks must be contiguous")
        if len({item.memory_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("Top32 memory IDs must be unique")


class Top32CandidateLoader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        self._connection = connection

    @classmethod
    def from_memory_store(cls, memory_store: object) -> "Top32CandidateLoader":
        connection = getattr(memory_store, "_connection", None)
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("memory_store must be a runtime MemoryStore")
        return cls(connection)

    def load(
        self, recall: GlobalRecallResult, query: SelectorQueryEncoding
    ) -> GlobalRecallTop32:
        if not isinstance(recall, GlobalRecallResult):
            raise TypeError("recall must be GlobalRecallResult")
        if not isinstance(query, SelectorQueryEncoding):
            raise TypeError("query must be SelectorQueryEncoding")
        if (
            recall.query_sha256 != query.query_sha256
            or recall.query_vector_sha256 != query.vector_sha256
        ):
            raise RecallCandidateError("recall result belongs to a different query")
        if not recall.candidates:
            return self._batch(recall, query, ())

        ids = [candidate.memory_id for candidate in recall.candidates]
        placeholders = ",".join("?" for _ in ids)
        rows = self._connection.execute(
            f"SELECT memory_id, status, version, representation_json "
            f"FROM memories WHERE memory_id IN ({placeholders})",
            ids,
        ).fetchall()
        by_id = {str(row[0]): row for row in rows}
        loaded = []
        for candidate in recall.candidates:
            row = by_id.get(candidate.memory_id)
            if row is None:
                raise RecallCandidateStaleError("Top32 memory disappeared after scan")
            memory = memory_representation_from_json(str(row[3]))
            revision = hashlib.sha256(canonical_memory_json(memory)).hexdigest()
            if (
                str(row[1]) not in MEMORY_INDEXABLE_STATUSES
                or int(row[2]) != candidate.memory_version
                or memory.status != str(row[1])
                or memory.version != candidate.memory_version
                or revision != candidate.source_revision
            ):
                raise RecallCandidateStaleError(
                    "Top32 memory status, version or source revision changed after scan"
                )
            loaded.append(
                Top32Candidate(
                    rank=candidate.rank,
                    memory_id=memory.memory_id,
                    memory_version=memory.version,
                    coarse_score=candidate.score,
                    memory_kind=memory.kind,
                    statement=memory.statement,
                    subject_type=memory.subject.subject_type,
                    subject_entity_id=memory.subject.entity_id,
                    subject_display_name=memory.subject.display_name,
                    temporal_relation=memory.temporal.relation,
                    temporal_source_text=memory.temporal.source_text,
                    temporal_start_at=memory.temporal.start_at,
                    temporal_end_at=memory.temporal.end_at,
                    temporal_timezone=memory.temporal.timezone,
                    epistemic_polarity=memory.epistemic.polarity,
                    epistemic_modality=memory.epistemic.modality,
                    winning_view_kind=candidate.winning_view_kind,
                    winning_view_text=candidate.winning_view_text,
                    source_revision=candidate.source_revision,
                )
            )
        return self._batch(recall, query, tuple(loaded))

    @staticmethod
    def _batch(
        recall: GlobalRecallResult,
        query: SelectorQueryEncoding,
        candidates: tuple[Top32Candidate, ...],
    ) -> GlobalRecallTop32:
        return GlobalRecallTop32(
            query_sha256=query.query_sha256,
            rendered_query=query.rendered_text,
            query_encoder_revision=query.encoder.revision,
            query_encoder_artifact_sha256=query.encoder.artifact_sha256,
            generation_id=recall.generation_id,
            global_pool_memory_count=recall.metrics.pool_memory_count,
            global_pool_view_count=recall.metrics.pool_view_count,
            candidates=candidates,
        )
