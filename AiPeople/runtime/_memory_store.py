from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from ._memory_contracts import (
    EvidenceSource,
    MemoryContractError,
    MemoryRepresentation,
    canonical_memory_json,
    memory_contract_to_dict,
    memory_representation_from_json,
    validate_memory_representation,
    validate_memory_transition,
)
from ._memory_materialization import MaterializationBatch


RUN_STATES = frozenset(
    {"pending", "failed_retryable", "failed_terminal", "committed"}
)
DECISION_REASON_CODES = frozenset(
    {
        "conflict_detected",
        "evidence_rejected",
        "evidence_validated",
        "needs_confirmation",
        "player_confirmed",
        "player_corrected",
        "superseded_by_correction",
    }
)


class MemoryStoreError(RuntimeError):
    pass


class MemoryRunConflictError(MemoryStoreError):
    pass


class MemoryIdempotencyConflictError(MemoryStoreError):
    pass


class MemoryDecisionError(MemoryStoreError):
    pass


@dataclass(frozen=True, slots=True)
class MemoryRunRecord:
    proposal_run_id: str
    conversation_id: str
    from_sequence_no: int
    through_sequence_no: int
    state: str
    attempt: int
    failure_code: str | None
    batch_sha256: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemoryCommitResult:
    run: MemoryRunRecord
    memories: tuple[MemoryRepresentation, ...]
    created: bool


Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class MemoryStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._connection = connection
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def begin_run(
        self,
        *,
        proposal_run_id: str,
        conversation_id: str,
        from_sequence_no: int,
        through_sequence_no: int,
        idempotency_key: str,
    ) -> MemoryRunRecord:
        _identifier(proposal_run_id, "proposal_run_id")
        _identifier(conversation_id, "conversation_id")
        _identifier(idempotency_key, "idempotency_key")
        if from_sequence_no <= 0 or through_sequence_no < from_sequence_no:
            raise ValueError("invalid proposal run sequence range")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing_key = self._run_transition_for_key(idempotency_key)
            if existing_key is not None:
                self._require_same_run_transition(
                    existing_key,
                    proposal_run_id=proposal_run_id,
                    conversation_id=conversation_id,
                    from_sequence_no=from_sequence_no,
                    through_sequence_no=through_sequence_no,
                    to_state="pending",
                )
                run = self._require_run(proposal_run_id)
                connection.commit()
                return run
            current = self._run(proposal_run_id)
            if current is not None:
                self._require_same_run_identity(
                    current, conversation_id, from_sequence_no, through_sequence_no
                )
                if current.state in ("pending", "committed"):
                    connection.commit()
                    return current
                if current.state == "failed_terminal":
                    raise MemoryRunConflictError("terminal memory run cannot be retried")
                from_state = current.state
                attempt = current.attempt + 1
                ordinal = self._next_run_ordinal(proposal_run_id)
            else:
                from_state = None
                attempt = 1
                ordinal = 1
            transition_id = f"memory-run-transition-{self._id_factory()}"
            now = _utc_z(self._clock())
            connection.execute(
                """
                INSERT INTO memory_run_transitions (
                    transition_id, proposal_run_id, conversation_id, ordinal,
                    from_state, to_state, from_sequence_no, through_sequence_no,
                    attempt, failure_code, batch_sha256, transitioned_at,
                    idempotency_key, schema_version
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, NULL, NULL, ?, ?, 1)
                """,
                (
                    transition_id,
                    proposal_run_id,
                    conversation_id,
                    ordinal,
                    from_state,
                    from_sequence_no,
                    through_sequence_no,
                    attempt,
                    now,
                    idempotency_key,
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_proposal_runs (
                    proposal_run_id, conversation_id, from_sequence_no,
                    through_sequence_no, state, attempt, failure_code,
                    batch_sha256, latest_transition_id, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?)
                ON CONFLICT(proposal_run_id) DO UPDATE SET
                    state='pending', attempt=excluded.attempt, failure_code=NULL,
                    batch_sha256=NULL, latest_transition_id=excluded.latest_transition_id,
                    updated_at=excluded.updated_at
                """,
                (
                    proposal_run_id,
                    conversation_id,
                    from_sequence_no,
                    through_sequence_no,
                    attempt,
                    transition_id,
                    now,
                ),
            )
            run = self._require_run(proposal_run_id)
            connection.commit()
            return run
        except Exception:
            connection.rollback()
            raise

    def record_failure(
        self,
        *,
        proposal_run_id: str,
        failure_code: str,
        retryable: bool,
        idempotency_key: str,
    ) -> MemoryRunRecord:
        _identifier(failure_code, "failure_code")
        _identifier(idempotency_key, "idempotency_key")
        if type(retryable) is not bool:
            raise TypeError("retryable must be a bool")
        to_state = "failed_retryable" if retryable else "failed_terminal"
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing_key = self._run_transition_for_key(idempotency_key)
            if existing_key is not None:
                if (
                    existing_key["proposal_run_id"] != proposal_run_id
                    or existing_key["to_state"] != to_state
                    or existing_key["failure_code"] != failure_code
                ):
                    raise MemoryIdempotencyConflictError(
                        "memory run idempotency key was reused"
                    )
                run = self._require_run(proposal_run_id)
                connection.commit()
                return run
            run = self._require_run(proposal_run_id)
            if run.state != "pending":
                raise MemoryRunConflictError("only pending memory run can fail")
            transition_id = f"memory-run-transition-{self._id_factory()}"
            now = _utc_z(self._clock())
            connection.execute(
                """
                INSERT INTO memory_run_transitions (
                    transition_id, proposal_run_id, conversation_id, ordinal,
                    from_state, to_state, from_sequence_no, through_sequence_no,
                    attempt, failure_code, batch_sha256, transitioned_at,
                    idempotency_key, schema_version
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, NULL, ?, ?, 1)
                """,
                (
                    transition_id,
                    run.proposal_run_id,
                    run.conversation_id,
                    self._next_run_ordinal(proposal_run_id),
                    to_state,
                    run.from_sequence_no,
                    run.through_sequence_no,
                    run.attempt,
                    failure_code,
                    now,
                    idempotency_key,
                ),
            )
            connection.execute(
                """
                UPDATE memory_proposal_runs
                SET state=?, failure_code=?, latest_transition_id=?, updated_at=?
                WHERE proposal_run_id=?
                """,
                (to_state, failure_code, transition_id, now, proposal_run_id),
            )
            result = self._require_run(proposal_run_id)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    def commit_batch(
        self,
        batch: MaterializationBatch,
        *,
        idempotency_key: str,
    ) -> MemoryCommitResult:
        if type(batch) is not MaterializationBatch:
            raise TypeError("batch must be MaterializationBatch")
        _identifier(idempotency_key, "idempotency_key")
        batch_sha256 = _batch_sha256(batch)
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            run = self._require_run(batch.proposal_run_id)
            if run.state == "committed":
                if run.batch_sha256 != batch_sha256:
                    raise MemoryIdempotencyConflictError(
                        "committed proposal run was retried with different content"
                    )
                memories = self.list_memories(proposal_run_id=batch.proposal_run_id)
                connection.commit()
                return MemoryCommitResult(run, memories, False)
            if run.state != "pending":
                raise MemoryRunConflictError("only pending memory run can commit")
            self._validate_batch(batch, run)
            now = _utc_z(self._clock())
            for memory in batch.representations:
                transition_id = f"memory-transition-{self._id_factory()}"
                connection.execute(
                    """
                    INSERT INTO memory_transitions (
                        transition_id, memory_id, proposal_run_id, conversation_id,
                        ordinal, from_status, to_status, representation_json,
                        reason_code, source_event_id, transitioned_at,
                        idempotency_key, schema_version
                    ) VALUES (?, ?, ?, ?, 1, NULL, 'proposed', ?,
                              'materialized', NULL, ?, ?, 1)
                    """,
                    (
                        transition_id,
                        memory.memory_id,
                        memory.proposal_run_id,
                        memory.conversation_id,
                        canonical_memory_json(memory).decode("utf-8"),
                        now,
                        memory.idempotency_key,
                    ),
                )
                self._write_projection(memory, transition_id, now)
            transition_id = f"memory-run-transition-{self._id_factory()}"
            connection.execute(
                """
                INSERT INTO memory_run_transitions (
                    transition_id, proposal_run_id, conversation_id, ordinal,
                    from_state, to_state, from_sequence_no, through_sequence_no,
                    attempt, failure_code, batch_sha256, transitioned_at,
                    idempotency_key, schema_version
                ) VALUES (?, ?, ?, ?, 'pending', 'committed', ?, ?, ?, NULL, ?, ?, ?, 1)
                """,
                (
                    transition_id,
                    run.proposal_run_id,
                    run.conversation_id,
                    self._next_run_ordinal(run.proposal_run_id),
                    run.from_sequence_no,
                    run.through_sequence_no,
                    run.attempt,
                    batch_sha256,
                    now,
                    idempotency_key,
                ),
            )
            connection.execute(
                """
                UPDATE memory_proposal_runs
                SET state='committed', failure_code=NULL, batch_sha256=?,
                    latest_transition_id=?, updated_at=?
                WHERE proposal_run_id=?
                """,
                (batch_sha256, transition_id, now, run.proposal_run_id),
            )
            committed_run = self._require_run(run.proposal_run_id)
            memories = self.list_memories(proposal_run_id=run.proposal_run_id)
            connection.commit()
            return MemoryCommitResult(committed_run, memories, True)
        except Exception:
            connection.rollback()
            raise

    def decide_memory(
        self,
        *,
        memory_id: str,
        to_status: str,
        reason_code: str,
        idempotency_key: str,
        source_event_id: str | None = None,
    ) -> MemoryRepresentation:
        if reason_code not in DECISION_REASON_CODES:
            raise MemoryDecisionError("unknown memory decision reason")
        _identifier(idempotency_key, "idempotency_key")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT * FROM memory_transitions WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["memory_id"] != memory_id
                    or existing["to_status"] != to_status
                    or existing["reason_code"] != reason_code
                    or existing["source_event_id"] != source_event_id
                ):
                    raise MemoryIdempotencyConflictError(
                        "memory decision idempotency key was reused"
                    )
                result = self._require_memory(memory_id)
                connection.commit()
                return result
            current = self._require_memory(memory_id)
            try:
                validate_memory_transition(current.status, to_status)
            except MemoryContractError as error:
                raise MemoryDecisionError(str(error)) from error
            targets = self._relation_targets(current)
            if to_status == "active" and any(
                targets[target].status in ("active", "disputed")
                for target in current.relations.contradicts
            ):
                raise MemoryDecisionError(
                    "contradicting memory must remain disputed until explicitly resolved"
                )
            now = _utc_z(self._clock())
            updated = replace(
                current,
                version=current.version + 1,
                status=to_status,
            )
            self._append_memory_transition(
                updated,
                from_status=current.status,
                reason_code=reason_code,
                source_event_id=source_event_id,
                idempotency_key=idempotency_key,
                transitioned_at=now,
            )
            if to_status == "active":
                for target_id in current.relations.supersedes:
                    target = targets[target_id]
                    try:
                        validate_memory_transition(target.status, "superseded")
                    except MemoryContractError as error:
                        raise MemoryDecisionError(str(error)) from error
                    superseded = replace(
                        target,
                        version=target.version + 1,
                        status="superseded",
                    )
                    derived_key = _derived_key(idempotency_key, target_id)
                    self._append_memory_transition(
                        superseded,
                        from_status=target.status,
                        reason_code="superseded_by_correction",
                        source_event_id=source_event_id,
                        idempotency_key=derived_key,
                        transitioned_at=now,
                    )
            result = self._require_memory(memory_id)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    def get_memory(self, memory_id: str) -> MemoryRepresentation | None:
        row = self._connection.execute(
            "SELECT representation_json FROM memories WHERE memory_id=?",
            (memory_id,),
        ).fetchone()
        return memory_representation_from_json(str(row[0])) if row is not None else None

    def list_memories(
        self,
        *,
        conversation_id: str | None = None,
        proposal_run_id: str | None = None,
    ) -> tuple[MemoryRepresentation, ...]:
        clauses = []
        values = []
        if conversation_id is not None:
            clauses.append("conversation_id=?")
            values.append(conversation_id)
        if proposal_run_id is not None:
            clauses.append("proposal_run_id=?")
            values.append(proposal_run_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT representation_json FROM memories" + where + " ORDER BY memory_id",
            values,
        ).fetchall()
        return tuple(memory_representation_from_json(str(row[0])) for row in rows)

    def rebuild_projection(self) -> tuple[MemoryRepresentation, ...]:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            run_rows = connection.execute(
                "SELECT * FROM memory_run_transitions ORDER BY proposal_run_id, ordinal"
            ).fetchall()
            connection.execute("DELETE FROM memory_proposal_runs")
            previous_runs: dict[str, dict[str, object]] = {}
            for row in run_rows:
                run_id = str(row["proposal_run_id"])
                prior = previous_runs.get(run_id)
                expected_ordinal = 1 if prior is None else int(prior["ordinal"]) + 1
                expected_from = None if prior is None else prior["state"]
                if int(row["ordinal"]) != expected_ordinal or row["from_state"] != expected_from:
                    raise MemoryStoreError("memory run transition chain is invalid")
                state = str(row["to_state"])
                if state not in RUN_STATES:
                    raise MemoryStoreError("memory run state is invalid")
                _validate_run_transition(expected_from, state)
                values = {
                    "ordinal": int(row["ordinal"]),
                    "state": state,
                    "attempt": int(row["attempt"]),
                }
                previous_runs[run_id] = values
                connection.execute(
                    """
                    INSERT INTO memory_proposal_runs (
                        proposal_run_id, conversation_id, from_sequence_no,
                        through_sequence_no, state, attempt, failure_code,
                        batch_sha256, latest_transition_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(proposal_run_id) DO UPDATE SET
                        state=excluded.state, attempt=excluded.attempt,
                        failure_code=excluded.failure_code,
                        batch_sha256=excluded.batch_sha256,
                        latest_transition_id=excluded.latest_transition_id,
                        updated_at=excluded.updated_at
                    """,
                    (
                        run_id,
                        str(row["conversation_id"]),
                        int(row["from_sequence_no"]),
                        int(row["through_sequence_no"]),
                        state,
                        int(row["attempt"]),
                        row["failure_code"],
                        row["batch_sha256"],
                        str(row["transition_id"]),
                        str(row["transitioned_at"]),
                    ),
                )

            rows = connection.execute(
                "SELECT * FROM memory_transitions ORDER BY memory_id, ordinal"
            ).fetchall()
            connection.execute("DELETE FROM memories")
            previous: dict[str, MemoryRepresentation] = {}
            for row in rows:
                memory = memory_representation_from_json(str(row["representation_json"]))
                prior = previous.get(memory.memory_id)
                expected_ordinal = 1 if prior is None else prior.version + 1
                if int(row["ordinal"]) != expected_ordinal or memory.version != expected_ordinal:
                    raise MemoryStoreError("memory transition version chain is invalid")
                if row["from_status"] != (prior.status if prior else None):
                    raise MemoryStoreError("memory transition status chain is invalid")
                if row["to_status"] != memory.status:
                    raise MemoryStoreError("memory transition payload status is invalid")
                if prior is None:
                    validate_memory_transition(None, memory.status)
                else:
                    validate_memory_transition(prior.status, memory.status)
                    if _memory_identity_payload(prior) != _memory_identity_payload(memory):
                        raise MemoryStoreError("memory transition changed immutable content")
                self._write_projection(
                    memory,
                    str(row["transition_id"]),
                    str(row["transitioned_at"]),
                )
                previous[memory.memory_id] = memory
            final_memories = {memory.memory_id: memory for memory in previous.values()}
            for memory in final_memories.values():
                validate_memory_representation(
                    memory,
                    evidence_sources=self._evidence_sources(memory),
                    relation_targets=final_memories,
                )
            result = self.list_memories()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise

    def _validate_batch(self, batch: MaterializationBatch, run: MemoryRunRecord) -> None:
        ordinals = tuple(memory.proposal_ordinal for memory in batch.representations)
        if ordinals != tuple(range(len(batch.representations))):
            raise MemoryStoreError("materialization batch ordinals must be contiguous")
        memory_ids = tuple(memory.memory_id for memory in batch.representations)
        if len(memory_ids) != len(set(memory_ids)):
            raise MemoryStoreError("materialization batch memory IDs must be unique")
        relation_targets = {
            memory.memory_id: memory
            for memory in self.list_memories(conversation_id=run.conversation_id)
        }
        for memory in batch.representations:
            if (
                memory.proposal_run_id != run.proposal_run_id
                or memory.conversation_id != run.conversation_id
                or memory.status != "proposed"
                or memory.version != 1
                or memory.idempotency_key
                != f"{run.proposal_run_id}:{memory.proposal_ordinal}"
            ):
                raise MemoryStoreError("materialized memory does not match proposal run")
            if self.get_memory(memory.memory_id) is not None:
                raise MemoryIdempotencyConflictError(
                    "memory_id already belongs to another committed proposal"
                )
            evidence_sources = self._evidence_sources(memory)
            try:
                validate_memory_representation(
                    memory,
                    evidence_sources=evidence_sources,
                    relation_targets=relation_targets,
                )
            except MemoryContractError as error:
                raise MemoryStoreError(str(error)) from error
            for target_id in (
                memory.relations.supersedes
                + memory.relations.contradicts
                + memory.relations.refines
            ):
                if relation_targets[target_id].status not in ("active", "disputed"):
                    raise MemoryStoreError(
                        "memory relation target must be active or disputed"
                    )

    def _evidence_sources(
        self, memory: MemoryRepresentation
    ) -> dict[str, EvidenceSource]:
        sources = {}
        for evidence in memory.evidence:
            row = self._connection.execute(
                "SELECT * FROM events WHERE event_id=?", (evidence.event_id,)
            ).fetchone()
            if row is None:
                continue
            payload = json.loads(str(row["payload_json"]))
            sources[evidence.event_id] = EvidenceSource(
                event_id=str(row["event_id"]),
                conversation_id=str(row["conversation_id"]),
                actor=str(row["actor"]),
                event_type=str(row["event_type"]),
                committed=payload.get("status") == "complete",
                text=str(payload.get("text", "")),
            )
        return sources

    def _relation_targets(
        self, memory: MemoryRepresentation
    ) -> dict[str, MemoryRepresentation]:
        target_ids = (
            memory.relations.supersedes
            + memory.relations.contradicts
            + memory.relations.refines
        )
        targets = {}
        for target_id in target_ids:
            target = self.get_memory(target_id)
            if target is None or target.conversation_id != memory.conversation_id:
                raise MemoryDecisionError("memory relation target is missing")
            targets[target_id] = target
        return targets

    def _append_memory_transition(
        self,
        memory: MemoryRepresentation,
        *,
        from_status: str,
        reason_code: str,
        source_event_id: str | None,
        idempotency_key: str,
        transitioned_at: str,
    ) -> None:
        transition_id = f"memory-transition-{self._id_factory()}"
        self._connection.execute(
            """
            INSERT INTO memory_transitions (
                transition_id, memory_id, proposal_run_id, conversation_id,
                ordinal, from_status, to_status, representation_json,
                reason_code, source_event_id, transitioned_at,
                idempotency_key, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                transition_id,
                memory.memory_id,
                memory.proposal_run_id,
                memory.conversation_id,
                memory.version,
                from_status,
                memory.status,
                canonical_memory_json(memory).decode("utf-8"),
                reason_code,
                source_event_id,
                transitioned_at,
                idempotency_key,
            ),
        )
        self._write_projection(memory, transition_id, transitioned_at)

    def _write_projection(
        self, memory: MemoryRepresentation, transition_id: str, updated_at: str
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO memories (
                memory_id, proposal_run_id, conversation_id, status, version,
                representation_json, latest_transition_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                status=excluded.status, version=excluded.version,
                representation_json=excluded.representation_json,
                latest_transition_id=excluded.latest_transition_id,
                updated_at=excluded.updated_at
            """,
            (
                memory.memory_id,
                memory.proposal_run_id,
                memory.conversation_id,
                memory.status,
                memory.version,
                canonical_memory_json(memory).decode("utf-8"),
                transition_id,
                updated_at,
            ),
        )

    def _run(self, proposal_run_id: str) -> MemoryRunRecord | None:
        row = self._connection.execute(
            "SELECT * FROM memory_proposal_runs WHERE proposal_run_id=?",
            (proposal_run_id,),
        ).fetchone()
        return self._run_record(row) if row is not None else None

    def _require_run(self, proposal_run_id: str) -> MemoryRunRecord:
        run = self._run(proposal_run_id)
        if run is None:
            raise MemoryRunConflictError("memory proposal run does not exist")
        return run

    def _require_memory(self, memory_id: str) -> MemoryRepresentation:
        memory = self.get_memory(memory_id)
        if memory is None:
            raise MemoryDecisionError("memory does not exist")
        return memory

    def _next_run_ordinal(self, proposal_run_id: str) -> int:
        return int(
            self._connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM memory_run_transitions WHERE proposal_run_id=?",
                (proposal_run_id,),
            ).fetchone()[0]
        )

    def _run_transition_for_key(self, key: str):
        return self._connection.execute(
            "SELECT * FROM memory_run_transitions WHERE idempotency_key=?", (key,)
        ).fetchone()

    @staticmethod
    def _require_same_run_identity(run, conversation_id, from_sequence_no, through_sequence_no):
        if (
            run.conversation_id != conversation_id
            or run.from_sequence_no != from_sequence_no
            or run.through_sequence_no != through_sequence_no
        ):
            raise MemoryRunConflictError("proposal_run_id belongs to another input window")

    @staticmethod
    def _require_same_run_transition(
        row,
        *,
        proposal_run_id,
        conversation_id,
        from_sequence_no,
        through_sequence_no,
        to_state,
    ):
        if (
            row["proposal_run_id"] != proposal_run_id
            or row["conversation_id"] != conversation_id
            or int(row["from_sequence_no"]) != from_sequence_no
            or int(row["through_sequence_no"]) != through_sequence_no
            or row["to_state"] != to_state
        ):
            raise MemoryIdempotencyConflictError("memory run idempotency key was reused")

    @staticmethod
    def _run_record(row) -> MemoryRunRecord:
        return MemoryRunRecord(
            proposal_run_id=str(row["proposal_run_id"]),
            conversation_id=str(row["conversation_id"]),
            from_sequence_no=int(row["from_sequence_no"]),
            through_sequence_no=int(row["through_sequence_no"]),
            state=str(row["state"]),
            attempt=int(row["attempt"]),
            failure_code=str(row["failure_code"]) if row["failure_code"] is not None else None,
            batch_sha256=str(row["batch_sha256"]) if row["batch_sha256"] is not None else None,
            updated_at=str(row["updated_at"]),
        )


def _batch_sha256(batch: MaterializationBatch) -> str:
    semantic = []
    for memory in batch.representations:
        value = memory_contract_to_dict(memory)
        value.pop("memory_id")
        value.pop("created_at")
        semantic.append(value)
    raw = json.dumps(
        semantic,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _memory_identity_payload(memory: MemoryRepresentation) -> dict[str, object]:
    value = memory_contract_to_dict(memory)
    value.pop("version")
    value.pop("status")
    return value


def _validate_run_transition(from_state: str | None, to_state: str) -> None:
    allowed = {
        None: {"pending"},
        "pending": {"failed_retryable", "failed_terminal", "committed"},
        "failed_retryable": {"pending"},
        "failed_terminal": set(),
        "committed": set(),
    }
    if to_state not in allowed[from_state]:
        raise MemoryStoreError(
            f"invalid memory run transition: {from_state} -> {to_state}"
        )


def _derived_key(decision_key: str, target_id: str) -> str:
    raw = f"{decision_key}\0{target_id}".encode("utf-8")
    return "memory-auto-supersede-" + hashlib.sha256(raw).hexdigest()


def _identifier(value: object, name: str) -> None:
    if type(value) is not str or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty identifier")


def _utc_z(value: datetime) -> str:
    if type(value) is not datetime or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    normalized = value.astimezone(timezone.utc)
    timespec = "microseconds" if normalized.microsecond else "seconds"
    return normalized.isoformat(timespec=timespec).replace("+00:00", "Z")
