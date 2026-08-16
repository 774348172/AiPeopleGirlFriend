from __future__ import annotations

import hashlib
import asyncio
import json
import math
import sqlite3
import struct
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Sequence

from runtime._memory_contracts import MemoryEvidence, MemoryProposalDraft
from runtime._memory_vectors import BGE_MODEL_ID, EmbeddingEncoder, VECTOR_DTYPE
from runtime._recall_candidates import GlobalRecallTop32, Top32Candidate
from runtime._reranker import MemoryReranker, RerankScore, validate_rerank_result
from runtime._selected_memory import (
    SELECTED_MEMORY_TOKEN_LIMIT,
    SelectedMemory,
    SelectedMemoryEvidence,
    SelectedMemoryFrame,
)

from .contracts import RuntimeSessionIdentity
from .memory_contracts import (
    MEMORY_V2_SCHEMA_VERSION,
    HeroineMemoryProposal,
    HeroineMemoryProposeRequest,
    HeroineMemoryProposer,
    HeroineMemoryRepresentation,
    HeroineMemorySourceEvent,
    canonical_heroine_memory_json,
    heroine_memory_from_json,
)


MEMORY_V2_VIEW_PROFILE_ID = "heroine-memory-selector-v2"
MEMORY_V2_TOP_K = 32
MEMORY_V2_SELECTED_LIMIT = 8


class HeroineMemoryRepositoryError(RuntimeError):
    pass


class HeroineMemoryOwnershipError(HeroineMemoryRepositoryError):
    pass


class HeroineMemoryEvidenceError(HeroineMemoryRepositoryError):
    pass


class HeroineMemoryConflictError(HeroineMemoryRepositoryError):
    pass


@dataclass(frozen=True, slots=True)
class HeroineMemoryIndexResult:
    generation_id: str
    memory_count: int
    view_count: int


@dataclass(frozen=True, slots=True)
class HeroineMemoryRecallResult:
    frame: SelectedMemoryFrame
    candidates: GlobalRecallTop32
    generation_id: str


@dataclass(frozen=True, slots=True)
class HeroineMemoryActivation:
    memory_id: str
    memory_version: int
    activation_score: float
    activated_game_time: datetime


@dataclass(frozen=True, slots=True)
class HeroineSelfTimelineEntry:
    memory_id: str
    memory_version: int
    game_time: datetime
    statement: str


@dataclass(frozen=True, slots=True)
class _SelectorView:
    memory_id: str
    memory_version: int
    view_ordinal: int
    view_kind: str
    text: str
    text_sha256: str
    source_revision: str


class HeroineMemoryRepository:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        save_id: str,
        world_id: str,
        owner_character_id: str,
        encoder: EmbeddingEncoder,
        reranker: MemoryReranker,
        lock: object | None = None,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        recorded_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        _identifier(save_id, "save_id")
        _identifier(world_id, "world_id")
        _identifier(owner_character_id, "owner_character_id")
        if not isinstance(encoder, EmbeddingEncoder):
            raise TypeError("encoder must implement EmbeddingEncoder")
        if not isinstance(reranker, MemoryReranker):
            raise TypeError("reranker must implement MemoryReranker")
        self._connection = connection
        self._save_id = save_id
        self._world_id = world_id
        self._owner_character_id = owner_character_id
        self._encoder = encoder
        self._reranker = reranker
        self._lock = lock if hasattr(lock, "__enter__") else threading.RLock()
        self._id_factory = id_factory
        self._recorded_clock = recorded_clock

    @classmethod
    def from_world_mind_store(
        cls,
        store: object,
        *,
        save_id: str,
        world_id: str,
        owner_character_id: str,
        encoder: EmbeddingEncoder,
        reranker: MemoryReranker,
    ) -> "HeroineMemoryRepository":
        connection = getattr(store, "_connection", None)
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("store must be a WorldMindStore")
        return cls(
            connection,
            save_id=save_id,
            world_id=world_id,
            owner_character_id=owner_character_id,
            encoder=encoder,
            reranker=reranker,
            lock=getattr(store, "_lock", None),
            id_factory=getattr(store, "_id_factory", lambda: str(uuid.uuid4())),
            recorded_clock=getattr(
                store, "_recorded_clock", lambda: datetime.now(timezone.utc)
            ),
        )

    @property
    def save_id(self) -> str:
        return self._save_id

    @property
    def world_id(self) -> str:
        return self._world_id

    @property
    def owner_character_id(self) -> str:
        return self._owner_character_id

    async def propose_from_request(
        self,
        request_id: str,
        proposer: HeroineMemoryProposer,
        *,
        participant_ids: tuple[str, ...] | None = None,
        observer_ids: tuple[str, ...] = (),
    ) -> tuple[HeroineMemoryProposal, ...]:
        _identifier(request_id, "request_id")
        rows = self._connection.execute(
            """
            SELECT event_id, protagonist_id FROM world_mind_events
            WHERE save_id = ? AND world_id = ? AND request_id = ?
            ORDER BY sequence_no
            """,
            (self._save_id, self._world_id, request_id),
        ).fetchall()
        if not rows:
            raise HeroineMemoryEvidenceError("request has no committed world-mind events")
        protagonists = {str(row["protagonist_id"]) for row in rows}
        if len(protagonists) != 1:
            raise HeroineMemoryEvidenceError("request event identity is inconsistent")
        participants = participant_ids or (
            next(iter(protagonists)),
            self._owner_character_id,
        )
        return await self.propose(
            tuple(str(row["event_id"]) for row in rows),
            proposer,
            participant_ids=participants,
            observer_ids=observer_ids,
        )

    async def propose(
        self,
        source_event_ids: tuple[str, ...],
        proposer: HeroineMemoryProposer,
        *,
        participant_ids: tuple[str, ...],
        observer_ids: tuple[str, ...] = (),
    ) -> tuple[HeroineMemoryProposal, ...]:
        if not isinstance(proposer, HeroineMemoryProposer):
            raise TypeError("proposer must implement HeroineMemoryProposer")
        events = self._load_source_events(source_event_ids)
        request = HeroineMemoryProposeRequest(
            save_id=self._save_id,
            world_id=self._world_id,
            owner_character_id=self._owner_character_id,
            source_events=events,
            existing_memories=self.list_memories(statuses=("active", "disputed")),
        )
        drafts = await proposer.propose(request)
        if not isinstance(drafts, Sequence) or any(
            not isinstance(item, MemoryProposalDraft) for item in drafts
        ):
            raise HeroineMemoryRepositoryError(
                "proposer must return a sequence of MemoryProposalDraft"
            )
        conversations = {event.conversation_id for event in events}
        conversation_id = next(iter(conversations)) if len(conversations) == 1 else None
        game_time = max(event.game_time for event in events)
        return tuple(
            HeroineMemoryProposal(
                proposal_id=self._id_factory(),
                save_id=self._save_id,
                world_id=self._world_id,
                owner_character_id=self._owner_character_id,
                conversation_id=conversation_id,
                source_event_ids=source_event_ids,
                participant_ids=participant_ids,
                observer_ids=observer_ids,
                game_time=game_time,
                draft=draft,
            )
            for draft in drafts
        )

    def materialize(self, proposal: HeroineMemoryProposal) -> HeroineMemoryRepresentation:
        self._require_proposal_ownership(proposal)
        sources = {
            event.event_id: event
            for event in self._load_source_events(proposal.source_event_ids)
        }
        evidence: list[MemoryEvidence] = []
        for quote in proposal.draft.evidence_quotes:
            source = sources.get(quote.event_id)
            if source is None:
                raise HeroineMemoryEvidenceError("evidence event is outside source_event_ids")
            if quote.start_hint is None:
                starts = _all_occurrences(source.text, quote.quote)
                if len(starts) != 1:
                    raise HeroineMemoryEvidenceError(
                        "evidence quote must occur exactly once or provide start_hint"
                    )
                start = starts[0]
            else:
                start = quote.start_hint
                if source.text[start : start + len(quote.quote)] != quote.quote:
                    raise HeroineMemoryEvidenceError(
                        "evidence quote does not match the committed event"
                    )
            evidence.append(
                MemoryEvidence(
                    event_id=quote.event_id,
                    role=quote.role,
                    excerpt_start=start,
                    excerpt_end=start + len(quote.quote),
                    excerpt=quote.quote,
                    excerpt_sha256=hashlib.sha256(quote.quote.encode("utf-8")).hexdigest(),
                )
            )
        return HeroineMemoryRepresentation(
            schema_version=MEMORY_V2_SCHEMA_VERSION,
            memory_id=self._id_factory(),
            version=1,
            save_id=self._save_id,
            world_id=self._world_id,
            owner_character_id=self._owner_character_id,
            conversation_id=proposal.conversation_id,
            source_event_ids=proposal.source_event_ids,
            participant_ids=proposal.participant_ids,
            observer_ids=proposal.observer_ids,
            game_time=proposal.game_time,
            kind=proposal.draft.kind,
            statement=proposal.draft.statement,
            subject=proposal.draft.subject,
            epistemic=proposal.draft.epistemic,
            temporal=proposal.draft.temporal,
            evidence=tuple(evidence),
            semantic_reason=proposal.draft.semantic_reason,
            confidence=proposal.draft.confidence,
            relations=proposal.draft.relation_suggestions,
            status="active",
            created_at=_recorded_time(self._recorded_clock()),
            idempotency_key=proposal.proposal_id,
        )

    def commit(
        self,
        memory: HeroineMemoryRepresentation,
        *,
        idempotency_key: str | None = None,
    ) -> HeroineMemoryRepresentation:
        self._require_memory_ownership(memory)
        key = idempotency_key or memory.idempotency_key
        _identifier(key, "idempotency_key")
        committed = replace(memory, idempotency_key=key)
        payload = canonical_heroine_memory_json(committed).decode("utf-8")
        revision = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    """
                    SELECT representation_json FROM memory_v2_transitions
                    WHERE save_id = ? AND owner_character_id = ? AND idempotency_key = ?
                    """,
                    (self._save_id, self._owner_character_id, key),
                ).fetchone()
                if existing is not None:
                    stored = heroine_memory_from_json(str(existing[0]))
                    if canonical_heroine_memory_json(stored) != canonical_heroine_memory_json(committed):
                        raise HeroineMemoryConflictError(
                            "idempotency key belongs to another memory"
                        )
                    self._connection.execute("COMMIT")
                    return stored
                duplicate = self._connection.execute(
                    """
                    SELECT 1 FROM memory_v2_current
                    WHERE save_id = ? AND owner_character_id = ? AND memory_id = ?
                    """,
                    (self._save_id, self._owner_character_id, committed.memory_id),
                ).fetchone()
                if duplicate is not None:
                    raise HeroineMemoryConflictError(
                        "memory_id already exists in repository"
                    )
                self._validate_relation_targets(committed)
                for target_id in committed.relations.supersedes:
                    self._supersede_current(target_id, committed)
                transition_id = self._id_factory()
                self._insert_transition(
                    transition_id=transition_id,
                    memory=committed,
                    from_version=None,
                    transition_kind="commit",
                )
                self._connection.execute(
                    """
                    INSERT INTO memory_v2_current(
                        save_id, world_id, owner_character_id, memory_id, version,
                        status, game_time, representation_json, source_revision,
                        last_transition_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._save_id,
                        self._world_id,
                        self._owner_character_id,
                        committed.memory_id,
                        committed.version,
                        committed.status,
                        _game_time(committed.game_time),
                        payload,
                        revision,
                        transition_id,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT OR REPLACE INTO memory_v2_self_timeline(
                        save_id, owner_character_id, memory_id, memory_version,
                        game_time, statement
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._save_id,
                        self._owner_character_id,
                        committed.memory_id,
                        committed.version,
                        _game_time(committed.game_time),
                        committed.statement,
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return committed

    def commit_r1_job(
        self,
        job_id: str,
        memories: Sequence[HeroineMemoryRepresentation],
    ) -> tuple[HeroineMemoryRepresentation, ...]:
        _identifier(job_id, "job_id")
        committed = tuple(memories)
        if not committed:
            raise ValueError("memories must not be empty")
        for memory in committed:
            self._require_memory_ownership(memory)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    """
                    SELECT state, save_id, world_id, character_id
                    FROM world_mind_r1_memory_jobs WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()
                if row is None or str(row["state"]) != "running":
                    raise HeroineMemoryRepositoryError(
                        "R1 memory job is not running"
                    )
                if (
                    str(row["save_id"]) != self._save_id
                    or str(row["world_id"]) != self._world_id
                    or str(row["character_id"]) != self._owner_character_id
                ):
                    raise HeroineMemoryOwnershipError(
                        "R1 memory job belongs to another repository"
                    )
                for ordinal, memory in enumerate(committed):
                    self._insert_memory_in_transaction(
                        replace(
                            memory,
                            idempotency_key=f"r1:{job_id}:{ordinal}",
                        )
                    )
                self._rebuild_in_transaction()
                cursor = self._connection.execute(
                    """
                    UPDATE world_mind_r1_memory_jobs
                    SET state = 'completed', failure_code = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'running'
                    """,
                    (_recorded_time(self._recorded_clock()), job_id),
                )
                if cursor.rowcount != 1:
                    raise HeroineMemoryRepositoryError(
                        "R1 memory job completion failed"
                    )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return committed

    def _insert_memory_in_transaction(
        self,
        memory: HeroineMemoryRepresentation,
    ) -> None:
        payload = canonical_heroine_memory_json(memory).decode("utf-8")
        duplicate = self._connection.execute(
            """
            SELECT 1 FROM memory_v2_current
            WHERE save_id = ? AND owner_character_id = ? AND memory_id = ?
            """,
            (self._save_id, self._owner_character_id, memory.memory_id),
        ).fetchone()
        if duplicate is not None:
            raise HeroineMemoryConflictError("memory_id already exists in repository")
        self._validate_relation_targets(memory)
        for target_id in memory.relations.supersedes:
            self._supersede_current(target_id, memory)
        transition_id = self._id_factory()
        self._insert_transition(
            transition_id=transition_id,
            memory=memory,
            from_version=None,
            transition_kind="commit",
        )
        self._connection.execute(
            """
            INSERT INTO memory_v2_current(
                save_id, world_id, owner_character_id, memory_id, version,
                status, game_time, representation_json, source_revision,
                last_transition_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._save_id,
                self._world_id,
                self._owner_character_id,
                memory.memory_id,
                memory.version,
                memory.status,
                _game_time(memory.game_time),
                payload,
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                transition_id,
            ),
        )
        self._connection.execute(
            """
            INSERT OR REPLACE INTO memory_v2_self_timeline(
                save_id, owner_character_id, memory_id, memory_version,
                game_time, statement
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self._save_id,
                self._owner_character_id,
                memory.memory_id,
                memory.version,
                _game_time(memory.game_time),
                memory.statement,
            ),
        )

    def get_memory(self, memory_id: str) -> HeroineMemoryRepresentation | None:
        _identifier(memory_id, "memory_id")
        row = self._connection.execute(
            """
            SELECT representation_json FROM memory_v2_current
            WHERE save_id = ? AND owner_character_id = ? AND memory_id = ?
            """,
            (self._save_id, self._owner_character_id, memory_id),
        ).fetchone()
        return None if row is None else heroine_memory_from_json(str(row[0]))

    def list_memories(
        self, *, statuses: tuple[str, ...] | None = None
    ) -> tuple[HeroineMemoryRepresentation, ...]:
        parameters: list[object] = [self._save_id, self._owner_character_id]
        where = "save_id = ? AND owner_character_id = ?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            parameters.extend(statuses)
        rows = self._connection.execute(
            f"""
            SELECT representation_json FROM memory_v2_current
            WHERE {where} ORDER BY game_time, memory_id
            """,
            parameters,
        ).fetchall()
        memories = tuple(heroine_memory_from_json(str(row[0])) for row in rows)
        for memory in memories:
            self._require_memory_ownership(memory)
        return memories

    def rebuild(self) -> HeroineMemoryIndexResult:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                result = self._rebuild_in_transaction()
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return result

    def _rebuild_in_transaction(self) -> HeroineMemoryIndexResult:
        rows = self._connection.execute(
            """
            SELECT representation_json FROM memory_v2_current
            WHERE save_id = ? AND owner_character_id = ?
              AND status IN ('active', 'disputed')
            ORDER BY game_time, memory_id
            """,
            (self._save_id, self._owner_character_id),
        ).fetchall()
        memories = tuple(heroine_memory_from_json(str(row[0])) for row in rows)
        for memory in memories:
            self._require_memory_ownership(memory)
        views = tuple(
            view for memory in memories for view in _build_selector_views(memory)
        )
        identity = self._encoder.identity
        if identity.model_id != BGE_MODEL_ID:
            raise HeroineMemoryRepositoryError("unsupported memory encoder")
        encoded = self._encoder.encode([view.text for view in views]) if views else ()
        if len(encoded) != len(views):
            raise HeroineMemoryRepositoryError(
                "encoder returned the wrong vector count"
            )
        normalized = tuple(
            _normalize_vector(item, identity.dimension) for item in encoded
        )
        generation_id = self._id_factory()
        recorded_at = _recorded_time(self._recorded_clock())
        self._connection.execute(
            """
            INSERT INTO memory_v2_vector_generations(
                generation_id, save_id, owner_character_id, view_profile_id,
                encoder_model_id, encoder_revision, encoder_artifact_sha256,
                dimension, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                self._save_id,
                self._owner_character_id,
                MEMORY_V2_VIEW_PROFILE_ID,
                identity.model_id,
                identity.revision,
                identity.artifact_sha256,
                identity.dimension,
                recorded_at,
                recorded_at,
            ),
        )
        for view, vector in zip(views, normalized, strict=True):
            blob = struct.pack(f"<{identity.dimension}f", *vector)
            self._connection.execute(
                """
                INSERT INTO memory_v2_selector_views(
                    save_id, owner_character_id, generation_id, memory_id,
                    memory_version, view_ordinal, view_kind, view_text,
                    text_sha256, source_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._save_id,
                    self._owner_character_id,
                    generation_id,
                    view.memory_id,
                    view.memory_version,
                    view.view_ordinal,
                    view.view_kind,
                    view.text,
                    view.text_sha256,
                    view.source_revision,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO memory_v2_embeddings(
                    save_id, owner_character_id, generation_id, memory_id,
                    memory_version, view_ordinal, dimension, dtype,
                    normalized, vector_blob, vector_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    self._save_id,
                    self._owner_character_id,
                    generation_id,
                    view.memory_id,
                    view.memory_version,
                    view.view_ordinal,
                    identity.dimension,
                    VECTOR_DTYPE,
                    blob,
                    hashlib.sha256(blob).hexdigest(),
                ),
            )
        self._connection.execute(
            """
            INSERT INTO memory_v2_active_generations(
                save_id, owner_character_id, generation_id
            ) VALUES (?, ?, ?)
            ON CONFLICT(save_id, owner_character_id)
            DO UPDATE SET generation_id = excluded.generation_id
            """,
            (self._save_id, self._owner_character_id, generation_id),
        )
        return HeroineMemoryIndexResult(generation_id, len(memories), len(views))

    async def recall(
        self,
        query_text: str,
        *,
        game_time: datetime,
        top_k: int = MEMORY_V2_TOP_K,
        selected_limit: int = MEMORY_V2_SELECTED_LIMIT,
    ) -> HeroineMemoryRecallResult:
        if type(query_text) is not str or not query_text.strip():
            raise ValueError("query_text must not be empty")
        _require_game_time(game_time)
        if not 1 <= top_k <= MEMORY_V2_TOP_K:
            raise ValueError("top_k must be in [1, 32]")
        if not 1 <= selected_limit <= MEMORY_V2_SELECTED_LIMIT:
            raise ValueError("selected_limit must be in [1, 8]")
        if self._index_is_stale():
            self.rebuild()
        generation_row = self._active_generation()
        if generation_row is None:
            generation_id = self.rebuild().generation_id
        else:
            generation_id = str(generation_row["generation_id"])
        identity = self._encoder.identity
        encoded_query = self._encoder.encode([query_text])
        if len(encoded_query) != 1:
            raise HeroineMemoryRepositoryError(
                "encoder returned the wrong query vector count"
            )
        query_vector = _normalize_vector(encoded_query[0], identity.dimension)
        rows = self._connection.execute(
            """
            SELECT v.memory_id, v.memory_version, v.view_ordinal, v.view_kind,
                   v.view_text, v.source_revision AS view_source_revision,
                   e.vector_blob, e.vector_sha256, e.dimension,
                   c.representation_json, c.status,
                   c.source_revision AS current_source_revision
            FROM memory_v2_selector_views AS v
            JOIN memory_v2_embeddings AS e
              ON e.save_id = v.save_id
             AND e.owner_character_id = v.owner_character_id
             AND e.generation_id = v.generation_id
             AND e.memory_id = v.memory_id
             AND e.memory_version = v.memory_version
             AND e.view_ordinal = v.view_ordinal
            JOIN memory_v2_current AS c
              ON c.save_id = v.save_id
             AND c.owner_character_id = v.owner_character_id
             AND c.memory_id = v.memory_id
             AND c.version = v.memory_version
            WHERE v.save_id = ? AND v.owner_character_id = ?
              AND v.generation_id = ? AND c.status IN ('active', 'disputed')
            ORDER BY v.memory_id, v.view_ordinal
            """,
            (self._save_id, self._owner_character_id, generation_id),
        ).fetchall()
        winners: dict[str, tuple[float, sqlite3.Row]] = {}
        for row in rows:
            blob = bytes(row["vector_blob"])
            if (
                int(row["dimension"]) != identity.dimension
                or len(blob) != identity.dimension * 4
                or hashlib.sha256(blob).hexdigest() != str(row["vector_sha256"])
                or str(row["view_source_revision"])
                != str(row["current_source_revision"])
            ):
                raise HeroineMemoryRepositoryError(
                    "memory vector integrity check failed"
                )
            vector = struct.unpack(f"<{identity.dimension}f", blob)
            score = math.fsum(
                left * right
                for left, right in zip(query_vector, vector, strict=True)
            )
            memory_id = str(row["memory_id"])
            current = winners.get(memory_id)
            if current is None or score > current[0]:
                winners[memory_id] = (score, row)
        ordered = sorted(
            winners.values(),
            key=lambda item: (-item[0], str(item[1]["memory_id"])),
        )[:top_k]
        candidates: list[Top32Candidate] = []
        for rank, (score, row) in enumerate(ordered, start=1):
            memory = heroine_memory_from_json(str(row["representation_json"]))
            self._require_memory_ownership(memory)
            candidates.append(
                Top32Candidate(
                    rank=rank,
                    memory_id=memory.memory_id,
                    memory_version=memory.version,
                    coarse_score=score,
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
                    winning_view_kind=str(row["view_kind"]),
                    winning_view_text=str(row["view_text"]),
                    source_revision=str(row["view_source_revision"]),
                )
            )
        batch = GlobalRecallTop32(
            query_sha256=hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
            rendered_query=query_text,
            query_encoder_revision=identity.revision,
            query_encoder_artifact_sha256=identity.artifact_sha256,
            generation_id=generation_id,
            global_pool_memory_count=len(winners),
            global_pool_view_count=len(rows),
            candidates=tuple(candidates),
        )
        if not batch.candidates:
            frame, activations = self._assemble_frame(batch, ())
            self._replace_working_activation(activations, game_time)
            return HeroineMemoryRecallResult(
                frame=frame,
                candidates=batch,
                generation_id=generation_id,
            )
        reranked = validate_rerank_result(
            batch, await self._reranker.rerank(batch)
        )
        frame, activations = self._assemble_frame(
            batch,
            reranked.selected_scores[:selected_limit],
        )
        self._replace_working_activation(activations, game_time)
        return HeroineMemoryRecallResult(
            frame=frame,
            candidates=batch,
            generation_id=generation_id,
        )

    def working_activation(self) -> tuple[HeroineMemoryActivation, ...]:
        rows = self._connection.execute(
            """
            SELECT memory_id, memory_version, activation_score, activated_game_time
            FROM memory_v2_working_activation
            WHERE save_id = ? AND owner_character_id = ?
            ORDER BY activation_score DESC, memory_id
            """,
            (self._save_id, self._owner_character_id),
        ).fetchall()
        return tuple(
            HeroineMemoryActivation(
                memory_id=str(row["memory_id"]),
                memory_version=int(row["memory_version"]),
                activation_score=float(row["activation_score"]),
                activated_game_time=_parse_game_time(
                    str(row["activated_game_time"])
                ),
            )
            for row in rows
        )

    def self_timeline(self) -> tuple[HeroineSelfTimelineEntry, ...]:
        rows = self._connection.execute(
            """
            SELECT memory_id, memory_version, game_time, statement
            FROM memory_v2_self_timeline
            WHERE save_id = ? AND owner_character_id = ?
            ORDER BY game_time, memory_id
            """,
            (self._save_id, self._owner_character_id),
        ).fetchall()
        return tuple(
            HeroineSelfTimelineEntry(
                memory_id=str(row["memory_id"]),
                memory_version=int(row["memory_version"]),
                game_time=_parse_game_time(str(row["game_time"])),
                statement=str(row["statement"]),
            )
            for row in rows
        )

    def _load_source_events(
        self, source_event_ids: tuple[str, ...]
    ) -> tuple[HeroineMemorySourceEvent, ...]:
        if not isinstance(source_event_ids, tuple) or not source_event_ids:
            raise TypeError("source_event_ids must be a non-empty tuple")
        if len(set(source_event_ids)) != len(source_event_ids):
            raise HeroineMemoryEvidenceError("source_event_ids must be unique")
        for event_id in source_event_ids:
            _identifier(event_id, "source_event_ids")
        placeholders = ",".join("?" for _ in source_event_ids)
        rows = self._connection.execute(
            f"""
            SELECT event_id, request_id, conversation_id, sequence_no, actor,
                   event_type, text, game_time
            FROM world_mind_events
            WHERE save_id = ? AND world_id = ?
              AND event_id IN ({placeholders})
            """,
            (self._save_id, self._world_id, *source_event_ids),
        ).fetchall()
        by_id = {str(row["event_id"]): row for row in rows}
        if set(by_id) != set(source_event_ids):
            raise HeroineMemoryEvidenceError(
                "source event is absent or belongs to another save/world"
            )
        return tuple(
            HeroineMemorySourceEvent(
                event_id=event_id,
                request_id=str(by_id[event_id]["request_id"]),
                conversation_id=str(by_id[event_id]["conversation_id"]),
                sequence_no=int(by_id[event_id]["sequence_no"]),
                actor=str(by_id[event_id]["actor"]),
                event_type=str(by_id[event_id]["event_type"]),
                text=str(by_id[event_id]["text"]),
                game_time=_parse_game_time(str(by_id[event_id]["game_time"])),
            )
            for event_id in source_event_ids
        )

    def _validate_relation_targets(
        self, memory: HeroineMemoryRepresentation
    ) -> None:
        target_ids = tuple(
            dict.fromkeys(
                memory.relations.supersedes
                + memory.relations.contradicts
                + memory.relations.refines
            )
        )
        if not target_ids:
            return
        placeholders = ",".join("?" for _ in target_ids)
        rows = self._connection.execute(
            f"""
            SELECT memory_id FROM memory_v2_current
            WHERE save_id = ? AND owner_character_id = ?
              AND memory_id IN ({placeholders})
            """,
            (self._save_id, self._owner_character_id, *target_ids),
        ).fetchall()
        if {str(row[0]) for row in rows} != set(target_ids):
            raise HeroineMemoryOwnershipError(
                "relation target is absent from the bound heroine repository"
            )

    def _supersede_current(
        self, target_id: str, source: HeroineMemoryRepresentation
    ) -> None:
        row = self._connection.execute(
            """
            SELECT representation_json FROM memory_v2_current
            WHERE save_id = ? AND owner_character_id = ? AND memory_id = ?
            """,
            (self._save_id, self._owner_character_id, target_id),
        ).fetchone()
        if row is None:
            raise HeroineMemoryOwnershipError(
                "superseded memory escaped repository"
            )
        target = heroine_memory_from_json(str(row[0]))
        if target.status == "superseded":
            return
        changed = replace(
            target,
            version=target.version + 1,
            status="superseded",
            idempotency_key=f"superseded:{source.memory_id}:{target.memory_id}",
        )
        transition_id = self._id_factory()
        self._insert_transition(
            transition_id=transition_id,
            memory=changed,
            from_version=target.version,
            transition_kind="status",
        )
        payload = canonical_heroine_memory_json(changed).decode("utf-8")
        self._connection.execute(
            """
            UPDATE memory_v2_current
            SET version = ?, status = ?, representation_json = ?,
                source_revision = ?, last_transition_id = ?
            WHERE save_id = ? AND owner_character_id = ? AND memory_id = ?
            """,
            (
                changed.version,
                changed.status,
                payload,
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                transition_id,
                self._save_id,
                self._owner_character_id,
                target_id,
            ),
        )

    def _insert_transition(
        self,
        *,
        transition_id: str,
        memory: HeroineMemoryRepresentation,
        from_version: int | None,
        transition_kind: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO memory_v2_transitions(
                transition_id, save_id, world_id, owner_character_id,
                memory_id, from_version, to_version, transition_kind,
                representation_json, source_event_ids_json, idempotency_key,
                recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transition_id,
                self._save_id,
                self._world_id,
                self._owner_character_id,
                memory.memory_id,
                from_version,
                memory.version,
                transition_kind,
                canonical_heroine_memory_json(memory).decode("utf-8"),
                json.dumps(memory.source_event_ids, ensure_ascii=False),
                memory.idempotency_key,
                _recorded_time(self._recorded_clock()),
            ),
        )

    def _index_is_stale(self) -> bool:
        generation = self._active_generation()
        if generation is None:
            return True
        current = self._connection.execute(
            """
            SELECT memory_id, version, source_revision
            FROM memory_v2_current
            WHERE save_id = ? AND owner_character_id = ?
              AND status IN ('active', 'disputed')
            ORDER BY memory_id
            """,
            (self._save_id, self._owner_character_id),
        ).fetchall()
        indexed = self._connection.execute(
            """
            SELECT DISTINCT memory_id, memory_version, source_revision
            FROM memory_v2_selector_views
            WHERE save_id = ? AND owner_character_id = ? AND generation_id = ?
            ORDER BY memory_id
            """,
            (
                self._save_id,
                self._owner_character_id,
                str(generation["generation_id"]),
            ),
        ).fetchall()
        current_keys = tuple(
            (
                str(row["memory_id"]),
                int(row["version"]),
                str(row["source_revision"]),
            )
            for row in current
        )
        indexed_keys = tuple(
            (
                str(row["memory_id"]),
                int(row["memory_version"]),
                str(row["source_revision"]),
            )
            for row in indexed
        )
        return current_keys != indexed_keys

    def _active_generation(self) -> sqlite3.Row | None:
        return self._connection.execute(
            """
            SELECT g.* FROM memory_v2_active_generations AS a
            JOIN memory_v2_vector_generations AS g
              ON g.generation_id = a.generation_id
             AND g.save_id = a.save_id
             AND g.owner_character_id = a.owner_character_id
            WHERE a.save_id = ? AND a.owner_character_id = ?
            """,
            (self._save_id, self._owner_character_id),
        ).fetchone()

    def _assemble_frame(
        self,
        batch: GlobalRecallTop32,
        scores: tuple[RerankScore, ...],
    ) -> tuple[SelectedMemoryFrame, tuple[RerankScore, ...]]:
        candidates = {candidate.memory_id: candidate for candidate in batch.candidates}
        selected: list[SelectedMemory] = []
        accepted_scores: list[RerankScore] = []
        token_count = 0
        truncated = False
        for score in scores:
            candidate = candidates[score.memory_id]
            memory = self.get_memory(candidate.memory_id)
            if memory is None or memory.version != candidate.memory_version:
                truncated = True
                continue
            estimate = _memory_token_estimate(memory)
            if token_count + estimate > SELECTED_MEMORY_TOKEN_LIMIT:
                truncated = True
                continue
            selected.append(
                SelectedMemory(
                    memory_id=memory.memory_id,
                    memory_version=memory.version,
                    kind=memory.kind,
                    statement=memory.statement,
                    subject_type=memory.subject.subject_type,
                    subject_display_name=memory.subject.display_name,
                    temporal_relation=memory.temporal.relation,
                    temporal_source_text=memory.temporal.source_text,
                    temporal_start_at=memory.temporal.start_at,
                    temporal_end_at=memory.temporal.end_at,
                    temporal_timezone=memory.temporal.timezone,
                    epistemic_polarity=memory.epistemic.polarity,
                    epistemic_modality=memory.epistemic.modality,
                    evidence=tuple(
                        SelectedMemoryEvidence(
                            item.event_id, item.role, item.excerpt
                        )
                        for item in memory.evidence
                    ),
                )
            )
            accepted_scores.append(score)
            token_count += estimate
        frame = SelectedMemoryFrame(
            selected_memories=tuple(selected),
            source_memory_ids=tuple(item.memory_id for item in selected),
            source_event_ids=tuple(
                dict.fromkeys(
                    evidence.event_id
                    for memory in selected
                    for evidence in memory.evidence
                )
            ),
            selector_version=MEMORY_V2_VIEW_PROFILE_ID,
            token_count=token_count,
            truncated=truncated,
        )
        return frame, tuple(accepted_scores)

    def _replace_working_activation(
        self, scores: tuple[RerankScore, ...], game_time: datetime
    ) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    DELETE FROM memory_v2_working_activation
                    WHERE save_id = ? AND owner_character_id = ?
                    """,
                    (self._save_id, self._owner_character_id),
                )
                for score in scores:
                    memory = self.get_memory(score.memory_id)
                    if memory is None:
                        continue
                    self._connection.execute(
                        """
                        INSERT INTO memory_v2_working_activation(
                            save_id, owner_character_id, memory_id, memory_version,
                            activation_score, activated_game_time
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self._save_id,
                            self._owner_character_id,
                            memory.memory_id,
                            memory.version,
                            score.activation_score,
                            _game_time(game_time),
                        ),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def _require_proposal_ownership(
        self, proposal: HeroineMemoryProposal
    ) -> None:
        if not isinstance(proposal, HeroineMemoryProposal):
            raise TypeError("proposal must be HeroineMemoryProposal")
        if (
            proposal.save_id != self._save_id
            or proposal.world_id != self._world_id
            or proposal.owner_character_id != self._owner_character_id
        ):
            raise HeroineMemoryOwnershipError(
                "proposal belongs to another repository"
            )

    def _require_memory_ownership(
        self, memory: HeroineMemoryRepresentation
    ) -> None:
        if not isinstance(memory, HeroineMemoryRepresentation):
            raise TypeError("memory must be HeroineMemoryRepresentation")
        if (
            memory.save_id != self._save_id
            or memory.world_id != self._world_id
            or memory.owner_character_id != self._owner_character_id
        ):
            raise HeroineMemoryOwnershipError(
                "memory belongs to another repository"
            )


class HeroineMemoryRepositoryFactory:
    def __init__(
        self,
        store: object,
        *,
        encoder: EmbeddingEncoder,
        reranker: MemoryReranker,
    ) -> None:
        if not isinstance(getattr(store, "_connection", None), sqlite3.Connection):
            raise TypeError("store must be a WorldMindStore")
        if not isinstance(encoder, EmbeddingEncoder):
            raise TypeError("encoder must implement EmbeddingEncoder")
        if not isinstance(reranker, MemoryReranker):
            raise TypeError("reranker must implement MemoryReranker")
        self._store = store
        self._encoder = encoder
        self._reranker = reranker
        self._repositories: dict[
            tuple[str, str, str], HeroineMemoryRepository
        ] = {}

    async def start(self) -> None:
        validate_encoder = getattr(self._encoder, "validate_runtime", None)
        if callable(validate_encoder):
            await asyncio.to_thread(validate_encoder)
        await self._reranker.start()
        validate_reranker = getattr(self._reranker, "validate_runtime", None)
        if callable(validate_reranker):
            await validate_reranker()

    async def close(self) -> None:
        await self._reranker.close()

    def open(
        self, session: RuntimeSessionIdentity
    ) -> HeroineMemoryRepository:
        if not isinstance(session, RuntimeSessionIdentity):
            raise TypeError("session must be RuntimeSessionIdentity")
        key = (
            session.save_id,
            session.world_id,
            session.active_character_id,
        )
        repository = self._repositories.get(key)
        if repository is None:
            repository = HeroineMemoryRepository.from_world_mind_store(
                self._store,
                save_id=session.save_id,
                world_id=session.world_id,
                owner_character_id=session.active_character_id,
                encoder=self._encoder,
                reranker=self._reranker,
            )
            self._repositories[key] = repository
        return repository


def _build_selector_views(
    memory: HeroineMemoryRepresentation,
) -> tuple[_SelectorView, ...]:
    source_revision = hashlib.sha256(
        canonical_heroine_memory_json(memory)
    ).hexdigest()
    values: list[tuple[str, str]] = [
        ("statement", " ".join(memory.statement.split()))
    ]
    if memory.temporal.source_text:
        values.append(
            ("temporal", " ".join(memory.temporal.source_text.split()))
        )
    values.extend(
        ("evidence", " ".join(item.excerpt.split()))
        for item in memory.evidence
    )
    unique = tuple(dict.fromkeys(item for item in values if item[1]))
    return tuple(
        _SelectorView(
            memory_id=memory.memory_id,
            memory_version=memory.version,
            view_ordinal=index,
            view_kind=kind,
            text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            source_revision=source_revision,
        )
        for index, (kind, text) in enumerate(unique)
    )


def _normalize_vector(
    raw: Sequence[float], dimension: int
) -> tuple[float, ...]:
    if len(raw) != dimension:
        raise HeroineMemoryRepositoryError(
            "encoder vector dimension mismatch"
        )
    values = tuple(float(value) for value in raw)
    if not all(math.isfinite(value) for value in values):
        raise HeroineMemoryRepositoryError(
            "encoder vector contains non-finite values"
        )
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0:
        raise HeroineMemoryRepositoryError("encoder vector has invalid norm")
    return tuple(value / norm for value in values)


def _all_occurrences(text: str, quote: str) -> tuple[int, ...]:
    starts: list[int] = []
    offset = 0
    while True:
        found = text.find(quote, offset)
        if found < 0:
            return tuple(starts)
        starts.append(found)
        offset = found + 1


def _memory_token_estimate(memory: HeroineMemoryRepresentation) -> int:
    characters = len(memory.statement) + sum(
        len(item.excerpt) for item in memory.evidence
    )
    return max(1, math.ceil(characters / 2))


def _identifier(value: object, name: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty identifier")


def _require_game_time(value: datetime) -> None:
    if type(value) is not datetime or value.tzinfo is not None:
        raise ValueError("game_time must be a naive game datetime")


def _game_time(value: datetime) -> str:
    _require_game_time(value)
    return value.isoformat(timespec="microseconds")


def _parse_game_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _require_game_time(parsed)
    return parsed


def _recorded_time(value: datetime) -> str:
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError("recorded clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
