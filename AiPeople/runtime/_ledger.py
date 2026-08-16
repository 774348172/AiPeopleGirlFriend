from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import UserMessage
from ._plans import (
    ACTIVE_PLAN_STATES,
    PLAN_KINDS,
    PlanRecord,
    PlanStateError,
    require_initial_state,
    require_transition,
)


Clock = Callable[[], datetime]
IdFactory = Callable[[], str]


class LedgerError(RuntimeError):
    pass


class MigrationError(LedgerError):
    pass


class RequestConflictError(LedgerError):
    pass


class DatabaseInUseError(LedgerError):
    pass


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: str
    request_id: str
    conversation_id: str
    sequence_no: int
    occurred_at: str
    recorded_at: str
    occurred_timezone: str
    actor: str
    event_type: str
    payload: dict[str, Any]
    source: str
    causation_event_id: str | None
    supersedes_event_id: str | None
    schema_version: int

    @property
    def text(self) -> str:
        return str(self.payload.get("text", ""))


@dataclass(frozen=True, slots=True)
class UserAppendResult:
    event: EventRecord
    created: bool
    epoch_id: str


@dataclass(frozen=True, slots=True)
class EpochSnapshot:
    epoch_id: str
    conversation_id: str
    history_events: tuple[EventRecord, ...]
    message_events: tuple[EventRecord, ...]


@dataclass(frozen=True, slots=True)
class RecallEpisode:
    anchor: EventRecord
    events: tuple[EventRecord, ...]
    matched_phrase: bool
    matched_time: bool
    time_distance_seconds: float


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_id() -> str:
    return str(uuid.uuid4())


def _utc_iso(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise LedgerError("stored event time must include a timezone")
    return parsed.astimezone(timezone.utc)


def _recall_episode_order(episode: RecallEpisode) -> tuple[object, ...]:
    has_both_roles = {event.actor for event in episode.events} >= {
        "user",
        "character",
    }
    return (
        not episode.matched_time,
        not episode.matched_phrase,
        episode.anchor.actor != "user",
        not has_both_roles,
        episode.time_distance_seconds,
        episode.anchor.sequence_no,
        episode.anchor.event_id,
    )


class EventLedger:
    def __init__(
        self,
        database_path: Path,
        connection: sqlite3.Connection,
        *,
        clock: Clock,
        id_factory: IdFactory,
        file_lock: _DatabaseFileLock,
    ) -> None:
        self.database_path = database_path
        self._connection = connection
        self._clock = clock
        self._id_factory = id_factory
        self._file_lock = file_lock
        self._closed = False

    @classmethod
    def open(
        cls,
        database_path: Path,
        *,
        clock: Clock = _utc_now,
        id_factory: IdFactory = _event_id,
    ) -> EventLedger:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_lock = _DatabaseFileLock.acquire(path.with_suffix(path.suffix + ".lock"))
        try:
            connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
        except Exception:
            file_lock.release()
            raise
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            ledger = cls(
                path,
                connection,
                clock=clock,
                id_factory=id_factory,
                file_lock=file_lock,
            )
            ledger._apply_migrations()
            return ledger
        except Exception:
            connection.close()
            file_lock.release()
            raise

    def close(self) -> None:
        if not self._closed:
            try:
                self._connection.close()
            finally:
                self._file_lock.release()
                self._closed = True

    def find_user_message(self, message: UserMessage) -> UserAppendResult | None:
        occurred_at = _utc_iso(message.occurred_at)
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """
                SELECT * FROM events
                WHERE request_id = ? AND actor = 'user' AND event_type = 'message'
                """,
                (message.request_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            event = self._record(row)
            if not self._same_user_message(event, message, occurred_at):
                raise RequestConflictError(
                    "request_id already belongs to a different user message"
                )
            epoch_id = self._ensure_event_epoch(event)
            connection.commit()
            return UserAppendResult(event, False, epoch_id)
        except Exception:
            connection.rollback()
            raise

    def preview_user_message(
        self, message: UserMessage, *, budget_version: int = 1
    ) -> EpochSnapshot:
        """Build an uncommitted current-turn snapshot for preflight measurement."""
        if budget_version <= 0:
            raise ValueError("budget_version must be positive")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            epoch_id = self._ensure_open_epoch(
                message.conversation_id, budget_version=budget_version
            )
            rows = connection.execute(
                """
                SELECT e.*
                FROM event_epochs AS ee
                JOIN events AS e ON e.event_id = ee.event_id
                WHERE ee.epoch_id = ?
                  AND e.event_type = 'message'
                  AND e.actor IN ('user', 'character')
                ORDER BY e.sequence_no
                """,
                (epoch_id,),
            ).fetchall()
            history = tuple(self._record(row) for row in rows)
            next_sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) + 1
                    FROM events WHERE conversation_id = ?
                    """,
                    (message.conversation_id,),
                ).fetchone()[0]
            )
            preview = EventRecord(
                event_id=f"preview-{message.request_id}",
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                sequence_no=next_sequence,
                occurred_at=_utc_iso(message.occurred_at),
                recorded_at=_utc_iso(self._clock()),
                occurred_timezone=message.timezone,
                actor="user",
                event_type="message",
                payload={"text": message.text, "status": "complete"},
                source=message.source,
                causation_event_id=None,
                supersedes_event_id=None,
                schema_version=1,
            )
            connection.commit()
            return EpochSnapshot(
                epoch_id=epoch_id,
                conversation_id=message.conversation_id,
                history_events=history,
                message_events=history + (preview,),
            )
        except Exception:
            connection.rollback()
            raise

    def append_user_message(
        self,
        message: UserMessage,
        *,
        rollover_from_epoch_id: str | None = None,
        budget_version: int = 1,
    ) -> UserAppendResult:
        if budget_version <= 0:
            raise ValueError("budget_version must be positive")
        occurred_at = _utc_iso(message.occurred_at)
        payload = {"text": message.text, "status": "complete"}
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                """
                SELECT * FROM events
                WHERE request_id = ? AND actor = 'user' AND event_type = 'message'
                """,
                (message.request_id,),
            ).fetchone()
            if existing is not None:
                event = self._record(existing)
                if not self._same_user_message(event, message, occurred_at):
                    raise RequestConflictError(
                        "request_id already belongs to a different user message"
                    )
                epoch_id = self._ensure_event_epoch(event)
                connection.commit()
                return UserAppendResult(event, False, epoch_id)

            if rollover_from_epoch_id is not None:
                self._close_epoch_for_rollover(
                    message.conversation_id, rollover_from_epoch_id
                )
            epoch_id = self._ensure_open_epoch(
                message.conversation_id, budget_version=budget_version
            )

            event = self._insert_event(
                request_id=message.request_id,
                conversation_id=message.conversation_id,
                occurred_at=occurred_at,
                occurred_timezone=message.timezone,
                actor="user",
                event_type="message",
                payload=payload,
                source=message.source,
                causation_event_id=None,
            )
            self._map_event_to_epoch(event.event_id, epoch_id)
            connection.commit()
            return UserAppendResult(event, True, epoch_id)
        except Exception:
            connection.rollback()
            raise

    def append_character_message(
        self,
        *,
        request_id: str,
        conversation_id: str,
        user_event_id: str,
        text: str,
        enqueue_memory_background: bool = False,
    ) -> EventRecord:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                """
                SELECT * FROM events
                WHERE request_id = ? AND actor = 'character' AND event_type = 'message'
                """,
                (request_id,),
            ).fetchone()
            if existing is not None:
                event = self._record(existing)
                if (
                    event.conversation_id != conversation_id
                    or event.causation_event_id != user_event_id
                    or event.text != text
                ):
                    raise RequestConflictError(
                        "request_id already has a different completed reply"
                    )
                self._ensure_event_epoch(event)
                if enqueue_memory_background:
                    self._enqueue_memory_background_job(event, user_event_id)
                connection.commit()
                return event

            self._require_user_cause(user_event_id, request_id, conversation_id)
            epoch_id = self._epoch_for_event(user_event_id)
            now = _utc_iso(self._clock())
            event = self._insert_event(
                request_id=request_id,
                conversation_id=conversation_id,
                occurred_at=now,
                occurred_timezone="UTC",
                actor="character",
                event_type="message",
                payload={"text": text, "status": "complete"},
                source="runtime",
                causation_event_id=user_event_id,
            )
            self._map_event_to_epoch(event.event_id, epoch_id)
            if enqueue_memory_background:
                self._enqueue_memory_background_job(event, user_event_id)
            connection.commit()
            return event
        except Exception:
            connection.rollback()
            raise

    def append_turn_status(
        self,
        *,
        request_id: str,
        conversation_id: str,
        user_event_id: str,
        event_type: str,
        code: str,
        partial_text: str,
    ) -> EventRecord:
        if event_type not in ("turn_failed", "generation_cancelled"):
            raise ValueError("unsupported turn status event")
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._require_user_cause(user_event_id, request_id, conversation_id)
            epoch_id = self._epoch_for_event(user_event_id)
            now = _utc_iso(self._clock())
            event = self._insert_event(
                request_id=request_id,
                conversation_id=conversation_id,
                occurred_at=now,
                occurred_timezone="UTC",
                actor="system",
                event_type=event_type,
                payload={
                    "code": code,
                    "partial_text": partial_text,
                    "emitted_chars": len(partial_text),
                    "status": "cancelled" if event_type == "generation_cancelled" else "failed",
                },
                source="runtime",
                causation_event_id=user_event_id,
            )
            self._map_event_to_epoch(event.event_id, epoch_id)
            connection.commit()
            return event
        except Exception:
            connection.rollback()
            raise

    def find_completed_reply(self, request_id: str) -> EventRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM events
            WHERE request_id = ? AND actor = 'character' AND event_type = 'message'
            """,
            (request_id,),
        ).fetchone()
        return self._record(row) if row is not None else None

    def create_plan(
        self,
        *,
        conversation_id: str,
        kind: str,
        description: str,
        due_at: datetime | None,
        timezone_name: str,
        source_event_id: str,
        idempotency_key: str,
        initial_state: str = "proposed",
    ) -> PlanRecord:
        normalized_description = description.strip()
        if kind not in PLAN_KINDS:
            raise ValueError("unsupported plan kind")
        if not 1 <= len(normalized_description) <= 500:
            raise ValueError("plan description must contain 1-500 characters")
        require_initial_state(initial_state)
        self._require_timezone(timezone_name)
        normalized_due_at = _utc_iso(due_at) if due_at is not None else None
        if kind == "reminder" and initial_state == "confirmed" and normalized_due_at is None:
            raise PlanStateError("confirmed reminder requires due_at")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key cannot be empty")

        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._plan_for_idempotency_key(idempotency_key)
            if existing is not None:
                transition = existing[1]
                if (
                    transition["conversation_id"] != conversation_id
                    or transition["kind"] != kind
                    or transition["description"] != normalized_description
                    or transition["due_at"] != normalized_due_at
                    or transition["timezone"] != timezone_name
                    or transition["source_event_id"] != source_event_id
                    or transition["to_state"] != initial_state
                ):
                    raise RequestConflictError("idempotency_key belongs to another plan command")
                connection.commit()
                return existing[0]

            self._require_plan_evidence(source_event_id, conversation_id)
            plan_id = f"plan-{self._id_factory()}"
            transition_id = f"plan-transition-{self._id_factory()}"
            transitioned_at = _utc_iso(self._clock())
            connection.execute(
                """
                INSERT INTO plan_transitions (
                    transition_id, plan_id, conversation_id, ordinal,
                    from_state, to_state, kind, description, due_at, timezone,
                    source_event_id, transitioned_at, idempotency_key, schema_version
                ) VALUES (?, ?, ?, 1, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    transition_id,
                    plan_id,
                    conversation_id,
                    initial_state,
                    kind,
                    normalized_description,
                    normalized_due_at,
                    timezone_name,
                    source_event_id,
                    transitioned_at,
                    idempotency_key,
                ),
            )
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, conversation_id, kind, description, due_at, timezone,
                    state, created_from_event_id, updated_from_event_id,
                    last_transition_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    plan_id,
                    conversation_id,
                    kind,
                    normalized_description,
                    normalized_due_at,
                    timezone_name,
                    initial_state,
                    source_event_id,
                    source_event_id,
                    transitioned_at,
                ),
            )
            connection.commit()
            return self._require_plan(plan_id)
        except Exception:
            connection.rollback()
            raise

    def transition_plan(
        self,
        *,
        plan_id: str,
        to_state: str,
        source_event_id: str,
        idempotency_key: str,
        due_at: datetime | None = None,
        timezone_name: str | None = None,
    ) -> PlanRecord:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key cannot be empty")
        requested_due_at = _utc_iso(due_at) if due_at is not None else None
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self._plan_for_idempotency_key(idempotency_key)
            if existing is not None:
                transition = existing[1]
                if (
                    transition["plan_id"] != plan_id
                    or transition["to_state"] != to_state
                    or transition["source_event_id"] != source_event_id
                    or (
                        requested_due_at is not None
                        and transition["due_at"] != requested_due_at
                    )
                    or (
                        timezone_name is not None
                        and transition["timezone"] != timezone_name
                    )
                ):
                    raise RequestConflictError("idempotency_key belongs to another plan command")
                connection.commit()
                return existing[0]

            current = self._require_plan(plan_id)
            require_transition(current.state, to_state)
            self._require_plan_evidence(source_event_id, current.conversation_id)
            effective_timezone = timezone_name or current.timezone
            self._require_timezone(effective_timezone)
            effective_due_at = requested_due_at or current.due_at
            if current.kind == "reminder" and to_state != "proposed" and effective_due_at is None:
                raise PlanStateError("confirmed reminder requires due_at")

            transitioned_at = _utc_iso(self._clock())
            next_version = current.version + 1
            connection.execute(
                """
                INSERT INTO plan_transitions (
                    transition_id, plan_id, conversation_id, ordinal,
                    from_state, to_state, kind, description, due_at, timezone,
                    source_event_id, transitioned_at, idempotency_key, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    f"plan-transition-{self._id_factory()}",
                    current.plan_id,
                    current.conversation_id,
                    next_version,
                    current.state,
                    to_state,
                    current.kind,
                    current.description,
                    effective_due_at,
                    effective_timezone,
                    source_event_id,
                    transitioned_at,
                    idempotency_key,
                ),
            )
            updated = connection.execute(
                """
                UPDATE plans
                SET due_at = ?, timezone = ?, state = ?, updated_from_event_id = ?,
                    last_transition_at = ?, version = ?
                WHERE plan_id = ? AND version = ?
                """,
                (
                    effective_due_at,
                    effective_timezone,
                    to_state,
                    source_event_id,
                    transitioned_at,
                    next_version,
                    current.plan_id,
                    current.version,
                ),
            )
            if updated.rowcount != 1:
                raise LedgerError("plan projection changed during transition")
            connection.commit()
            return self._require_plan(plan_id)
        except Exception:
            connection.rollback()
            raise

    def list_active_plans(self, conversation_id: str) -> tuple[PlanRecord, ...]:
        placeholders = ",".join("?" for _ in ACTIVE_PLAN_STATES)
        rows = self._connection.execute(
            f"""
            SELECT * FROM plans
            WHERE conversation_id = ? AND state IN ({placeholders})
            ORDER BY due_at IS NULL, due_at, last_transition_at, plan_id
            """,
            (conversation_id, *sorted(ACTIVE_PLAN_STATES)),
        ).fetchall()
        return tuple(self._plan_record(row) for row in rows)

    def rebuild_plan_projection(self) -> int:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DELETE FROM plans")
            connection.execute(
                """
                INSERT INTO plans (
                    plan_id, conversation_id, kind, description, due_at, timezone,
                    state, created_from_event_id, updated_from_event_id,
                    last_transition_at, version
                )
                SELECT latest.plan_id, latest.conversation_id, latest.kind,
                       latest.description, latest.due_at, latest.timezone,
                       latest.to_state, first.source_event_id, latest.source_event_id,
                       latest.transitioned_at, latest.ordinal
                FROM plan_transitions AS latest
                JOIN plan_transitions AS first
                  ON first.plan_id = latest.plan_id AND first.ordinal = 1
                WHERE latest.ordinal = (
                    SELECT MAX(candidate.ordinal)
                    FROM plan_transitions AS candidate
                    WHERE candidate.plan_id = latest.plan_id
                )
                ORDER BY latest.plan_id
                """
            )
            count = int(connection.execute("SELECT COUNT(*) FROM plans").fetchone()[0])
            connection.commit()
            return count
        except Exception:
            connection.rollback()
            raise

    def load_epoch_snapshot(self, user_event_id: str) -> EpochSnapshot:
        epoch_id = self._epoch_for_event(user_event_id)
        rows = self._connection.execute(
            """
            SELECT e.*
            FROM event_epochs AS ee
            JOIN events AS e ON e.event_id = ee.event_id
            WHERE ee.epoch_id = ?
              AND e.event_type = 'message'
              AND e.actor IN ('user', 'character')
            ORDER BY e.sequence_no
            """,
            (epoch_id,),
        ).fetchall()
        events = tuple(self._record(row) for row in rows)
        current = next(
            (event for event in events if event.event_id == user_event_id), None
        )
        if current is None or current.actor != "user":
            raise LedgerError("current user event is not a complete epoch message")
        if any(event.conversation_id != current.conversation_id for event in events):
            raise LedgerError("epoch contains events from multiple conversations")
        return EpochSnapshot(
            epoch_id=epoch_id,
            conversation_id=current.conversation_id,
            history_events=tuple(
                event for event in events if event.event_id != user_event_id
            ),
            message_events=events,
        )

    def rebuild_epoch_projection(self, conversation_id: str) -> str | None:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "DELETE FROM conversation_epochs WHERE conversation_id = ?",
                (conversation_id,),
            )
            has_events = connection.execute(
                "SELECT 1 FROM events WHERE conversation_id = ? LIMIT 1",
                (conversation_id,),
            ).fetchone()
            epoch_id = (
                self._ensure_open_epoch(conversation_id)
                if has_events is not None
                else None
            )
            connection.commit()
            return epoch_id
        except Exception:
            connection.rollback()
            raise

    def list_events(self, conversation_id: str | None = None) -> list[EventRecord]:
        if conversation_id is None:
            rows = self._connection.execute(
                "SELECT * FROM events ORDER BY conversation_id, sequence_no"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT * FROM events WHERE conversation_id = ? ORDER BY sequence_no",
                (conversation_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def get_event(self, event_id: str) -> EventRecord | None:
        row = self._connection.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._record(row) if row is not None else None

    def memory_store(self):
        from ._memory_store import MemoryStore

        return MemoryStore(
            self._connection,
            clock=self._clock,
            id_factory=self._id_factory,
        )

    def memory_background_queue(self):
        from ._memory_scheduler import MemoryBackgroundQueue

        return MemoryBackgroundQueue(self._connection, self._clock)

    def _enqueue_memory_background_job(
        self, assistant_event: EventRecord, user_event_id: str
    ) -> None:
        user_event = self.get_event(user_event_id)
        if user_event is None:
            raise LedgerError("memory background user event is missing")
        job_id = f"memory-propose:{assistant_event.event_id}"
        now = _utc_iso(self._clock())
        self._connection.execute(
            """
            INSERT INTO memory_background_jobs (
                job_id, queue_ordinal, mode, proposal_run_id, request_id, conversation_id,
                user_event_id, assistant_event_id, from_sequence_no,
                through_sequence_no, state, attempt, failure_code,
                created_at, updated_at
            ) VALUES (?, (SELECT COALESCE(MAX(queue_ordinal), 0) + 1 FROM memory_background_jobs),
                      'MEMORY_PROPOSE', ?, ?, ?, ?, ?, ?, ?,
                      'pending', 0, NULL, ?, ?)
            ON CONFLICT(job_id) DO NOTHING
            """,
            (
                job_id,
                job_id,
                assistant_event.request_id,
                assistant_event.conversation_id,
                user_event_id,
                assistant_event.event_id,
                user_event.sequence_no,
                assistant_event.sequence_no,
                now,
                now,
            ),
        )

    def search_messages(self, query: str) -> list[str]:
        rows = self._connection.execute(
            "SELECT event_id FROM event_fts WHERE event_fts MATCH ? ORDER BY rank",
            (query,),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def find_recall_episodes(
        self,
        *,
        conversation_id: str,
        excluded_epoch_id: str,
        phrases: tuple[str, ...],
        time_ranges: tuple[tuple[datetime, datetime], ...],
        candidate_limit: int,
    ) -> tuple[RecallEpisode, ...]:
        """Return bounded old-epoch message windows for explicit literal cues."""
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")

        literal_phrases = tuple(
            dict.fromkeys(
                phrase.strip()[:80]
                for phrase in phrases
                if isinstance(phrase, str) and len(phrase.strip()) >= 3
            )
        )[:8]
        utc_ranges = tuple(
            (_utc_iso(start), _utc_iso(end), start, end)
            for start, end in time_ranges[:8]
            if start.utcoffset() is not None
            and end.utcoffset() is not None
            and start < end
        )
        if not literal_phrases and not utc_ranges:
            return ()

        candidates: dict[str, dict[str, object]] = {}
        for phrase in literal_phrases:
            match_expression = '"' + phrase.replace('"', '""') + '"'
            rows = self._connection.execute(
                """
                SELECT e.*
                FROM event_fts
                JOIN events AS e ON e.event_id = event_fts.event_id
                JOIN event_epochs AS ee ON ee.event_id = e.event_id
                WHERE event_fts MATCH ?
                  AND e.conversation_id = ?
                  AND ee.epoch_id <> ?
                  AND e.event_type = 'message'
                  AND e.actor IN ('user', 'character')
                ORDER BY bm25(event_fts), e.sequence_no, e.event_id
                LIMIT ?
                """,
                (
                    match_expression,
                    conversation_id,
                    excluded_epoch_id,
                    candidate_limit,
                ),
            ).fetchall()
            for row in rows:
                entry = candidates.setdefault(
                    str(row["event_id"]),
                    {"row": row, "phrase": False, "time": False, "distance": float("inf")},
                )
                entry["phrase"] = True

        for start_iso, end_iso, start, end in utc_ranges:
            midpoint = start + (end - start) / 2
            midpoint_iso = _utc_iso(midpoint)
            rows = self._connection.execute(
                """
                SELECT e.*
                FROM events AS e
                JOIN event_epochs AS ee ON ee.event_id = e.event_id
                WHERE e.conversation_id = ?
                  AND ee.epoch_id <> ?
                  AND e.event_type = 'message'
                  AND e.actor IN ('user', 'character')
                  AND e.occurred_at >= ?
                  AND e.occurred_at < ?
                ORDER BY ABS(julianday(e.occurred_at) - julianday(?)),
                         e.sequence_no, e.event_id
                LIMIT ?
                """,
                (
                    conversation_id,
                    excluded_epoch_id,
                    start_iso,
                    end_iso,
                    midpoint_iso,
                    candidate_limit,
                ),
            ).fetchall()
            for row in rows:
                entry = candidates.setdefault(
                    str(row["event_id"]),
                    {"row": row, "phrase": False, "time": False, "distance": float("inf")},
                )
                entry["time"] = True
                occurred_at = _parse_utc(str(row["occurred_at"]))
                entry["distance"] = min(
                    float(entry["distance"]),
                    abs((occurred_at - midpoint.astimezone(timezone.utc)).total_seconds()),
                )

        episodes: list[RecallEpisode] = []
        for entry in candidates.values():
            anchor = self._record(entry["row"])
            window = self._recall_window(anchor.event_id, excluded_epoch_id)
            episodes.append(
                RecallEpisode(
                    anchor=anchor,
                    events=window,
                    matched_phrase=bool(entry["phrase"]),
                    matched_time=bool(entry["time"]),
                    time_distance_seconds=float(entry["distance"]),
                )
            )

        episodes.sort(key=_recall_episode_order)
        return tuple(episodes[:candidate_limit])

    def _apply_migrations(self) -> None:
        connection = self._connection
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            ) STRICT
            """
        )
        migration_dir = Path(__file__).with_name("migrations")
        all_files = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
        known = {int(path.name.split("_", 1)[0]) for path in all_files}
        files = [
            path
            for path in all_files
            if int(path.name.split("_", 1)[0]) <= 8
        ]
        applied = {
            int(row[0])
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        unknown = applied - known
        if unknown:
            raise MigrationError(f"database contains unknown migrations: {sorted(unknown)}")

        for path in files:
            version = int(path.name.split("_", 1)[0])
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            applied_at = _utc_iso(self._clock()).replace("'", "''")
            script = (
                "BEGIN IMMEDIATE;\n"
                + sql
                + f"\nINSERT INTO schema_migrations(version, applied_at) "
                f"VALUES ({version}, '{applied_at}');\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.Error as error:
                if connection.in_transaction:
                    connection.rollback()
                raise MigrationError(f"migration {path.name} failed") from error

    def _insert_event(
        self,
        *,
        request_id: str,
        conversation_id: str,
        occurred_at: str,
        occurred_timezone: str,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        source: str,
        causation_event_id: str | None,
        supersedes_event_id: str | None = None,
    ) -> EventRecord:
        next_sequence = int(
            self._connection.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM events WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
        )
        event_id = self._id_factory()
        recorded_at = _utc_iso(self._clock())
        self._connection.execute(
            """
            INSERT INTO events (
                event_id, request_id, conversation_id, sequence_no,
                occurred_at, recorded_at, occurred_timezone,
                actor, event_type, payload_json, source,
                causation_event_id, supersedes_event_id, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                request_id,
                conversation_id,
                next_sequence,
                occurred_at,
                recorded_at,
                occurred_timezone,
                actor,
                event_type,
                _json(payload),
                source,
                causation_event_id,
                supersedes_event_id,
                1,
            ),
        )
        row = self._connection.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise LedgerError("inserted event could not be read back")
        return self._record(row)

    def _ensure_open_epoch(
        self, conversation_id: str, *, budget_version: int = 1
    ) -> str:
        existing = self._connection.execute(
            """
            SELECT epoch_id
            FROM conversation_epochs
            WHERE conversation_id = ? AND state = 'open'
            """,
            (conversation_id,),
        ).fetchone()
        if existing is not None:
            epoch_id = str(existing["epoch_id"])
            self._map_unassigned_events(conversation_id, epoch_id)
            return epoch_id

        ordinal = int(
            self._connection.execute(
                """
                SELECT COALESCE(MAX(ordinal), 0) + 1
                FROM conversation_epochs WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()[0]
        )
        if ordinal == 1:
            sequence_sql = "SELECT COALESCE(MIN(sequence_no), 1) FROM events WHERE conversation_id = ?"
        else:
            sequence_sql = "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM events WHERE conversation_id = ?"
        opened_sequence_no = int(
            self._connection.execute(sequence_sql, (conversation_id,)).fetchone()[0]
        )
        epoch_id = f"epoch-{self._id_factory()}"
        self._connection.execute(
            """
            INSERT INTO conversation_epochs (
                epoch_id, conversation_id, ordinal, state,
                opened_sequence_no, budget_version, created_at
            ) VALUES (?, ?, ?, 'open', ?, ?, ?)
            """,
            (
                epoch_id,
                conversation_id,
                ordinal,
                opened_sequence_no,
                budget_version,
                _utc_iso(self._clock()),
            ),
        )
        self._map_unassigned_events(conversation_id, epoch_id)
        return epoch_id

    def _close_epoch_for_rollover(
        self, conversation_id: str, expected_epoch_id: str
    ) -> None:
        row = self._connection.execute(
            """
            SELECT ce.epoch_id, MAX(e.sequence_no) AS closed_sequence_no
            FROM conversation_epochs AS ce
            LEFT JOIN event_epochs AS ee ON ee.epoch_id = ce.epoch_id
            LEFT JOIN events AS e ON e.event_id = ee.event_id
            WHERE ce.conversation_id = ? AND ce.epoch_id = ? AND ce.state = 'open'
            GROUP BY ce.epoch_id
            """,
            (conversation_id, expected_epoch_id),
        ).fetchone()
        if row is None:
            raise LedgerError("rollover source is not the current open epoch")
        if row["closed_sequence_no"] is None:
            raise LedgerError("cannot roll over an empty epoch")
        self._connection.execute(
            """
            UPDATE conversation_epochs
            SET state = 'closed', closed_sequence_no = ?,
                close_reason = 'context_budget', closed_at = ?
            WHERE epoch_id = ? AND state = 'open'
            """,
            (
                int(row["closed_sequence_no"]),
                _utc_iso(self._clock()),
                expected_epoch_id,
            ),
        )

    def _recall_window(
        self, anchor_event_id: str, excluded_epoch_id: str
    ) -> tuple[EventRecord, ...]:
        anchor_row = self._connection.execute(
            """
            SELECT e.*, ee.epoch_id, ee.ordinal_in_epoch
            FROM events AS e
            JOIN event_epochs AS ee ON ee.event_id = e.event_id
            WHERE e.event_id = ? AND ee.epoch_id <> ?
            """,
            (anchor_event_id, excluded_epoch_id),
        ).fetchone()
        if anchor_row is None:
            raise LedgerError("recall anchor is missing or belongs to current epoch")
        previous = self._connection.execute(
            """
            SELECT e.*
            FROM event_epochs AS ee
            JOIN events AS e ON e.event_id = ee.event_id
            WHERE ee.epoch_id = ?
              AND e.event_type = 'message'
              AND e.actor IN ('user', 'character')
              AND ee.ordinal_in_epoch < ?
            ORDER BY ee.ordinal_in_epoch DESC
            LIMIT 1
            """,
            (
                str(anchor_row["epoch_id"]),
                int(anchor_row["ordinal_in_epoch"]),
            ),
        ).fetchone()
        following = self._connection.execute(
            """
            SELECT e.*
            FROM event_epochs AS ee
            JOIN events AS e ON e.event_id = ee.event_id
            WHERE ee.epoch_id = ?
              AND e.event_type = 'message'
              AND e.actor IN ('user', 'character')
              AND ee.ordinal_in_epoch > ?
            ORDER BY ee.ordinal_in_epoch
            LIMIT 1
            """,
            (
                str(anchor_row["epoch_id"]),
                int(anchor_row["ordinal_in_epoch"]),
            ),
        ).fetchone()
        events = [self._record(anchor_row)]
        if previous is not None:
            events.append(self._record(previous))
        if following is not None:
            events.append(self._record(following))
        events.sort(key=lambda event: (event.sequence_no, event.event_id))
        return tuple(events)

    def _ensure_event_epoch(self, event: EventRecord) -> str:
        existing = self._connection.execute(
            "SELECT epoch_id FROM event_epochs WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            return str(existing["epoch_id"])
        if event.causation_event_id is not None:
            epoch_id = self._epoch_for_event(event.causation_event_id)
        else:
            epoch_id = self._ensure_open_epoch(event.conversation_id)
        self._map_event_to_epoch(event.event_id, epoch_id)
        return epoch_id

    def _epoch_for_event(self, event_id: str) -> str:
        row = self._connection.execute(
            """
            SELECT ee.epoch_id, e.conversation_id
            FROM events AS e
            LEFT JOIN event_epochs AS ee ON ee.event_id = e.event_id
            WHERE e.event_id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise LedgerError("event does not exist")
        if row["epoch_id"] is not None:
            return str(row["epoch_id"])
        epoch_id = self._ensure_open_epoch(str(row["conversation_id"]))
        self._map_event_to_epoch(event_id, epoch_id)
        return epoch_id

    def _map_unassigned_events(self, conversation_id: str, epoch_id: str) -> None:
        rows = self._connection.execute(
            """
            SELECT e.event_id
            FROM events AS e
            LEFT JOIN event_epochs AS ee ON ee.event_id = e.event_id
            WHERE e.conversation_id = ? AND ee.event_id IS NULL
            ORDER BY e.sequence_no
            """,
            (conversation_id,),
        ).fetchall()
        for row in rows:
            self._map_event_to_epoch(str(row["event_id"]), epoch_id)

    def _map_event_to_epoch(self, event_id: str, epoch_id: str) -> None:
        existing = self._connection.execute(
            "SELECT epoch_id FROM event_epochs WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if existing is not None:
            if str(existing["epoch_id"]) != epoch_id:
                raise LedgerError("event is already mapped to another epoch")
            return
        ordinal = int(
            self._connection.execute(
                """
                SELECT COALESCE(MAX(ordinal_in_epoch), 0) + 1
                FROM event_epochs WHERE epoch_id = ?
                """,
                (epoch_id,),
            ).fetchone()[0]
        )
        self._connection.execute(
            """
            INSERT INTO event_epochs (
                event_id, epoch_id, ordinal_in_epoch, estimated_tokens
            ) VALUES (?, ?, ?, 0)
            """,
            (event_id, epoch_id, ordinal),
        )

    def _require_user_cause(
        self, user_event_id: str, request_id: str, conversation_id: str
    ) -> None:
        row = self._connection.execute(
            """
            SELECT request_id, conversation_id, actor, event_type
            FROM events WHERE event_id = ?
            """,
            (user_event_id,),
        ).fetchone()
        if row is None:
            raise LedgerError("causation user event does not exist")
        if (
            row["request_id"] != request_id
            or row["conversation_id"] != conversation_id
            or row["actor"] != "user"
            or row["event_type"] != "message"
        ):
            raise RequestConflictError("causation event does not match the turn")

    def _require_plan_evidence(
        self, source_event_id: str, conversation_id: str
    ) -> EventRecord:
        row = self._connection.execute(
            "SELECT * FROM events WHERE event_id = ?", (source_event_id,)
        ).fetchone()
        if row is None:
            raise LedgerError("plan evidence event does not exist")
        event = self._record(row)
        if event.conversation_id != conversation_id:
            raise RequestConflictError(
                "plan evidence belongs to another conversation"
            )
        return event

    def _require_plan(self, plan_id: str) -> PlanRecord:
        row = self._connection.execute(
            "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise LedgerError("plan does not exist")
        return self._plan_record(row)

    def _plan_for_idempotency_key(
        self, idempotency_key: str
    ) -> tuple[PlanRecord, sqlite3.Row] | None:
        transition = self._connection.execute(
            "SELECT * FROM plan_transitions WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if transition is None:
            return None
        return self._require_plan(str(transition["plan_id"])), transition

    @staticmethod
    def _require_timezone(timezone_name: str) -> None:
        if not timezone_name.strip():
            raise ValueError("timezone cannot be empty")
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone must be a valid IANA timezone") from error

    @staticmethod
    def _plan_record(row: sqlite3.Row) -> PlanRecord:
        return PlanRecord(
            plan_id=str(row["plan_id"]),
            conversation_id=str(row["conversation_id"]),
            kind=str(row["kind"]),
            description=str(row["description"]),
            due_at=str(row["due_at"]) if row["due_at"] is not None else None,
            timezone=str(row["timezone"]),
            state=str(row["state"]),
            created_from_event_id=str(row["created_from_event_id"]),
            updated_from_event_id=str(row["updated_from_event_id"]),
            last_transition_at=str(row["last_transition_at"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _same_user_message(
        event: EventRecord, message: UserMessage, occurred_at: str
    ) -> bool:
        return (
            event.conversation_id == message.conversation_id
            and event.occurred_at == occurred_at
            and event.occurred_timezone == message.timezone
            and event.source == message.source
            and event.text == message.text
        )

    @staticmethod
    def _record(row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            event_id=str(row["event_id"]),
            request_id=str(row["request_id"]),
            conversation_id=str(row["conversation_id"]),
            sequence_no=int(row["sequence_no"]),
            occurred_at=str(row["occurred_at"]),
            recorded_at=str(row["recorded_at"]),
            occurred_timezone=str(row["occurred_timezone"]),
            actor=str(row["actor"]),
            event_type=str(row["event_type"]),
            payload=json.loads(str(row["payload_json"])),
            source=str(row["source"]),
            causation_event_id=(
                str(row["causation_event_id"])
                if row["causation_event_id"] is not None
                else None
            ),
            supersedes_event_id=(
                str(row["supersedes_event_id"])
                if row["supersedes_event_id"] is not None
                else None
            ),
            schema_version=int(row["schema_version"]),
        )


class _DatabaseFileLock:
    def __init__(self, path: Path, handle) -> None:
        self.path = path
        self._handle = handle
        self._released = False

    @classmethod
    def acquire(cls, path: Path) -> _DatabaseFileLock:
        handle = path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise DatabaseInUseError(f"database is already in use: {path}") from error
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise DatabaseInUseError(f"database is already in use: {path}") from error
            return cls(path, handle)
        except Exception:
            handle.close()
            raise

    def release(self) -> None:
        if self._released:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._released = True
