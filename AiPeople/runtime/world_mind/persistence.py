from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .canon_loader import InitialHeroineRuntimeSeed
from .contracts import RuntimeSessionIdentity
from .model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    POST_REPLY_WORLD_MIND_RECONCILE,
    ContinuityReviewResult,
    GameReplyResult,
    HeroineDiegeticAction,
    MindAdvanceResult,
    ReconcileMindResult,
    ReconcileSourceTurn,
)
from .model_payloads import (
    continuity_result_record,
    game_reply_result_record,
    mind_result_record,
    reconcile_result_record,
)
from .state import (
    ActiveSceneState,
    HeroineRuntime,
    LiveWorldState,
    LivingMind,
    ProtagonistLiveState,
    ReconcileWorldSnapshot,
    RelationshipState,
    TurnWorldSnapshot,
    WorldEventDelta,
)
from .turn_request import TurnRequest


class WorldMindStoreError(RuntimeError):
    pass


class TurnRequestConflictError(WorldMindStoreError):
    pass


class MindStateConflictError(WorldMindStoreError):
    pass


@dataclass(frozen=True, slots=True)
class ClockAnchorRecord:
    save_id: str
    world_id: str
    anchor_game_time: datetime
    time_scale: float
    running: bool
    state_version: int


@dataclass(frozen=True, slots=True)
class CommittedTurn:
    transaction_id: str
    request_id: str
    user_event_id: str
    assistant_event_id: str
    text: str
    committed_game_time: datetime
    heroine_runtime: HeroineRuntime


@dataclass(frozen=True, slots=True)
class ProjectionRebuildResult:
    live_world: LiveWorldState
    heroine_runtime: HeroineRuntime


@dataclass(frozen=True, slots=True)
class ReconcileJob:
    job_id: str
    dedupe_key: str
    session: RuntimeSessionIdentity
    mode: str
    required_before_next_turn: bool
    source_request_id: str | None
    source_turn: ReconcileSourceTurn | None
    elapsed_runtime_seconds: float
    missed_intervals: int
    state: str
    attempt: int
    failure_code: str | None
    created_game_time: datetime
    available_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class R1MemoryJob:
    job_id: str
    dedupe_key: str
    session: RuntimeSessionIdentity
    source_request_id: str
    user_event_id: str
    assistant_event_id: str
    state: str
    attempt: int
    failure_code: str | None
    created_game_time: datetime
    available_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReconcileCheckpoint:
    save_id: str
    world_id: str
    character_id: str
    last_world_version: int
    last_game_time: datetime
    last_job_id: str | None


@dataclass(frozen=True, slots=True)
class ReconcileDecision:
    job_id: str
    mode: str
    snapshot_id: str
    from_mind_version: int
    to_mind_version: int
    operation: str
    reconcile_result: dict[str, Any]
    continuity_review: dict[str, Any]
    committed_game_time: datetime


class WorldMindStore:
    def __init__(
        self,
        database_path: Path,
        connection: sqlite3.Connection,
        *,
        id_factory: Callable[[], str],
        recorded_clock: Callable[[], datetime],
        before_turn_commit: Callable[[], None] | None,
        before_reconcile_commit: Callable[[], None] | None,
    ) -> None:
        self.database_path = database_path
        self._connection = connection
        self._id_factory = id_factory
        self._recorded_clock = recorded_clock
        self._before_turn_commit = before_turn_commit
        self._before_reconcile_commit = before_reconcile_commit
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def open(
        cls,
        database_path: Path,
        *,
        id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
        recorded_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        before_turn_commit: Callable[[], None] | None = None,
        before_reconcile_commit: Callable[[], None] | None = None,
    ) -> WorldMindStore:
        path = Path(database_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            path,
            isolation_level=None,
            timeout=5.0,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        store = cls(
            path,
            connection,
            id_factory=id_factory,
            recorded_clock=recorded_clock,
            before_turn_commit=before_turn_commit,
            before_reconcile_commit=before_reconcile_commit,
        )
        try:
            store._apply_migrations()
        except Exception:
            connection.close()
            raise
        return store

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.close()
            self._closed = True

    def load_clock_anchor(self, save_id: str) -> ClockAnchorRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM game_clock_anchors WHERE save_id = ?",
                (save_id,),
            ).fetchone()
        if row is None:
            return None
        return ClockAnchorRecord(
            save_id=str(row["save_id"]),
            world_id=str(row["world_id"]),
            anchor_game_time=_parse_game_time(str(row["anchor_game_time"])),
            time_scale=float(row["time_scale"]),
            running=bool(row["running"]),
            state_version=int(row["state_version"]),
        )

    def save_clock_anchor(
        self,
        *,
        save_id: str,
        world_id: str,
        anchor_game_time: datetime,
        time_scale: float,
        running: bool,
    ) -> ClockAnchorRecord:
        with self._lock:
            existing = self._connection.execute(
                "SELECT state_version, world_id FROM game_clock_anchors WHERE save_id = ?",
                (save_id,),
            ).fetchone()
            if existing is not None and str(existing["world_id"]) != world_id:
                raise WorldMindStoreError("clock anchor world identity mismatch")
            version = 1 if existing is None else int(existing["state_version"]) + 1
            self._connection.execute(
                """
                INSERT INTO game_clock_anchors(
                    save_id, world_id, anchor_game_time, time_scale,
                    running, state_version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(save_id) DO UPDATE SET
                    anchor_game_time = excluded.anchor_game_time,
                    time_scale = excluded.time_scale,
                    running = excluded.running,
                    state_version = excluded.state_version,
                    updated_at = excluded.updated_at
                """,
                (
                    save_id,
                    world_id,
                    _game_time_text(anchor_game_time),
                    time_scale,
                    int(running),
                    version,
                    _recorded_time_text(self._recorded_clock()),
                ),
            )
        return ClockAnchorRecord(
            save_id=save_id,
            world_id=world_id,
            anchor_game_time=anchor_game_time,
            time_scale=time_scale,
            running=running,
            state_version=version,
        )

    def put_live_world(
        self,
        session: RuntimeSessionIdentity,
        protagonist: ProtagonistLiveState,
        scene: ActiveSceneState,
        game_time: datetime,
    ) -> tuple[LiveWorldState, WorldEventDelta | None]:
        if protagonist.protagonist_id != session.protagonist_id:
            raise WorldMindStoreError("protagonist state identity mismatch")
        protagonist_json = _json(_protagonist_payload(protagonist))
        scene_json = _json(_scene_payload(scene))
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                protagonist_row = connection.execute(
                    "SELECT * FROM protagonist_live_states WHERE save_id = ?",
                    (session.save_id,),
                ).fetchone()
                scene_row = connection.execute(
                    "SELECT * FROM active_scene_states WHERE save_id = ?",
                    (session.save_id,),
                ).fetchone()
                if protagonist_row is not None:
                    self._require_live_identity(protagonist_row, session)
                if scene_row is not None and str(scene_row["world_id"]) != session.world_id:
                    raise WorldMindStoreError("scene world identity mismatch")
                if (
                    protagonist_row is not None
                    and scene_row is not None
                    and str(protagonist_row["payload_json"]) == protagonist_json
                    and str(scene_row["payload_json"]) == scene_json
                ):
                    connection.commit()
                    return self._live_world_from_rows(
                        session,
                        protagonist_row,
                        scene_row,
                    ), None

                version_row = connection.execute(
                    "SELECT live_world_version FROM save_runtime_versions WHERE save_id = ?",
                    (session.save_id,),
                ).fetchone()
                from_version = 0 if version_row is None else int(version_row[0])
                to_version = from_version + 1
                changed_fields = []
                if protagonist_row is None or str(protagonist_row["payload_json"]) != protagonist_json:
                    changed_fields.append("protagonist")
                if scene_row is None or str(scene_row["payload_json"]) != scene_json:
                    changed_fields.append("scene")
                game_time_text = _game_time_text(game_time)
                connection.execute(
                    """
                    INSERT INTO protagonist_live_states(
                        save_id, world_id, protagonist_id, version,
                        payload_json, changed_game_time
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(save_id) DO UPDATE SET
                        world_id = excluded.world_id,
                        protagonist_id = excluded.protagonist_id,
                        version = excluded.version,
                        payload_json = excluded.payload_json,
                        changed_game_time = excluded.changed_game_time
                    """,
                    (session.save_id, session.world_id, session.protagonist_id,
                     to_version, protagonist_json, game_time_text),
                )
                connection.execute(
                    """
                    INSERT INTO active_scene_states(
                        save_id, world_id, version, payload_json, changed_game_time
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(save_id) DO UPDATE SET
                        world_id = excluded.world_id,
                        version = excluded.version,
                        payload_json = excluded.payload_json,
                        changed_game_time = excluded.changed_game_time
                    """,
                    (session.save_id, session.world_id, to_version,
                     scene_json, game_time_text),
                )
                self._upsert_save_versions(
                    connection,
                    session.save_id,
                    session.world_id,
                    live_world_version=to_version,
                )
                event_id = self._id_factory()
                connection.execute(
                    """
                    INSERT INTO world_event_deltas(
                        event_id, save_id, world_id, from_version, to_version,
                        changed_fields_json, game_time, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (event_id, session.save_id, session.world_id, from_version,
                     to_version, _json(changed_fields), game_time_text,
                     _recorded_time_text(self._recorded_clock())),
                )
                connection.execute(
                    """
                    INSERT INTO world_state_transitions(
                        transition_id, save_id, world_id, protagonist_id,
                        version, protagonist_payload_json, scene_payload_json,
                        changed_game_time, source, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'program_update', ?)
                    """,
                    (
                        event_id,
                        session.save_id,
                        session.world_id,
                        session.protagonist_id,
                        to_version,
                        protagonist_json,
                        scene_json,
                        game_time_text,
                        _recorded_time_text(self._recorded_clock()),
                    ),
                )
                connection.commit()
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("live world update failed") from error
        state = LiveWorldState(
            save_id=session.save_id,
            world_id=session.world_id,
            protagonist_id=session.protagonist_id,
            version=to_version,
            protagonist=protagonist,
            scene=scene,
            last_changed_game_time=game_time,
        )
        delta = WorldEventDelta(
            event_id=event_id,
            save_id=session.save_id,
            from_version=from_version,
            to_version=to_version,
            changed_fields=tuple(changed_fields),
            game_time=game_time,
        )
        return state, delta

    def load_live_world(self, session: RuntimeSessionIdentity) -> LiveWorldState | None:
        with self._lock:
            protagonist_row = self._connection.execute(
                "SELECT * FROM protagonist_live_states WHERE save_id = ?",
                (session.save_id,),
            ).fetchone()
            scene_row = self._connection.execute(
                "SELECT * FROM active_scene_states WHERE save_id = ?",
                (session.save_id,),
            ).fetchone()
        if protagonist_row is None and scene_row is None:
            return None
        if protagonist_row is None or scene_row is None:
            raise WorldMindStoreError("live world state is incomplete")
        self._require_live_identity(protagonist_row, session)
        if str(scene_row["world_id"]) != session.world_id:
            raise WorldMindStoreError("scene world identity mismatch")
        return self._live_world_from_rows(session, protagonist_row, scene_row)

    def get_or_create_heroine_runtime(
        self,
        session: RuntimeSessionIdentity,
        seed: InitialHeroineRuntimeSeed,
    ) -> HeroineRuntime:
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM heroine_runtime_current
                    WHERE save_id = ? AND character_id = ?
                    """,
                    (session.save_id, session.active_character_id),
                ).fetchone()
                if row is not None:
                    runtime = _heroine_runtime_from_row(row)
                    self._require_heroine_identity(runtime, session)
                    connection.commit()
                    return runtime
                runtime = HeroineRuntime.from_seed(session, seed)
                connection.execute(
                    """
                    INSERT INTO heroine_runtime_current(
                        save_id, world_id, protagonist_id, character_id,
                        version, payload_json, last_transition_id
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (session.save_id, session.world_id, session.protagonist_id,
                     session.active_character_id, runtime.version,
                     _json(_heroine_runtime_payload(runtime))),
                )
                connection.execute(
                    """
                    INSERT INTO heroine_runtime_seeds(
                        seed_id, save_id, world_id, protagonist_id,
                        character_id, version, payload_json, source, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'character_package', ?)
                    """,
                    (
                        self._id_factory(),
                        session.save_id,
                        session.world_id,
                        session.protagonist_id,
                        session.active_character_id,
                        runtime.version,
                        _json(_heroine_runtime_payload(runtime)),
                        _recorded_time_text(self._recorded_clock()),
                    ),
                )
                self._upsert_save_versions(
                    connection,
                    session.save_id,
                    session.world_id,
                    mind_commit_version=runtime.version,
                )
                connection.commit()
                return runtime
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError(
                    "heroine runtime initialization failed"
                ) from error

    def load_heroine_runtime(
        self,
        session: RuntimeSessionIdentity,
    ) -> HeroineRuntime | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM heroine_runtime_current
                WHERE save_id = ? AND character_id = ?
                """,
                (session.save_id, session.active_character_id),
            ).fetchone()
        if row is None:
            return None
        runtime = _heroine_runtime_from_row(row)
        self._require_heroine_identity(runtime, session)
        return runtime

    def rebuild_runtime_projections(
        self,
        session: RuntimeSessionIdentity,
    ) -> ProjectionRebuildResult:
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                world_row = connection.execute(
                    """
                    SELECT * FROM world_state_transitions
                    WHERE save_id = ?
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (session.save_id,),
                ).fetchone()
                if world_row is None:
                    raise WorldMindStoreError("live world rebuild source is missing")
                if str(world_row["world_id"]) != session.world_id:
                    raise WorldMindStoreError("live world rebuild world mismatch")
                if str(world_row["protagonist_id"]) != session.protagonist_id:
                    raise WorldMindStoreError("live world rebuild protagonist mismatch")

                seed_row = connection.execute(
                    """
                    SELECT version, payload_json FROM heroine_runtime_seeds
                    WHERE save_id = ? AND character_id = ?
                    ORDER BY version DESC
                    LIMIT 1
                    """,
                    (session.save_id, session.active_character_id),
                ).fetchone()
                transition_row = connection.execute(
                    """
                    SELECT transition_id, to_version, payload_json
                    FROM heroine_mind_transitions
                    WHERE save_id = ? AND character_id = ?
                    ORDER BY to_version DESC
                    LIMIT 1
                    """,
                    (session.save_id, session.active_character_id),
                ).fetchone()
                if seed_row is None and transition_row is None:
                    raise WorldMindStoreError("heroine runtime rebuild source is missing")

                seed_version = 0 if seed_row is None else int(seed_row["version"])
                transition_version = (
                    0 if transition_row is None else int(transition_row["to_version"])
                )
                if transition_version >= seed_version and transition_row is not None:
                    heroine_payload_json = str(transition_row["payload_json"])
                    heroine_version = transition_version
                    last_transition_id = str(transition_row["transition_id"])
                else:
                    heroine_payload_json = str(seed_row["payload_json"])
                    heroine_version = seed_version
                    last_transition_id = None

                protagonist_json = str(world_row["protagonist_payload_json"])
                scene_json = str(world_row["scene_payload_json"])
                world_version = int(world_row["version"])
                changed_game_time = str(world_row["changed_game_time"])
                heroine_runtime = _heroine_runtime_from_payload(
                    json.loads(heroine_payload_json)
                )
                self._require_heroine_identity(heroine_runtime, session)
                if heroine_runtime.version != heroine_version:
                    raise WorldMindStoreError("heroine rebuild version mismatch")

                connection.execute(
                    """
                    INSERT INTO protagonist_live_states(
                        save_id, world_id, protagonist_id, version,
                        payload_json, changed_game_time
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(save_id) DO UPDATE SET
                        world_id = excluded.world_id,
                        protagonist_id = excluded.protagonist_id,
                        version = excluded.version,
                        payload_json = excluded.payload_json,
                        changed_game_time = excluded.changed_game_time
                    """,
                    (
                        session.save_id,
                        session.world_id,
                        session.protagonist_id,
                        world_version,
                        protagonist_json,
                        changed_game_time,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO active_scene_states(
                        save_id, world_id, version, payload_json,
                        changed_game_time
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(save_id) DO UPDATE SET
                        world_id = excluded.world_id,
                        version = excluded.version,
                        payload_json = excluded.payload_json,
                        changed_game_time = excluded.changed_game_time
                    """,
                    (
                        session.save_id,
                        session.world_id,
                        world_version,
                        scene_json,
                        changed_game_time,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO heroine_runtime_current(
                        save_id, world_id, protagonist_id, character_id,
                        version, payload_json, last_transition_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(save_id, character_id) DO UPDATE SET
                        world_id = excluded.world_id,
                        protagonist_id = excluded.protagonist_id,
                        version = excluded.version,
                        payload_json = excluded.payload_json,
                        last_transition_id = excluded.last_transition_id
                    """,
                    (
                        session.save_id,
                        session.world_id,
                        session.protagonist_id,
                        session.active_character_id,
                        heroine_runtime.version,
                        heroine_payload_json,
                        last_transition_id,
                    ),
                )
                self._upsert_save_versions(
                    connection,
                    session.save_id,
                    session.world_id,
                    live_world_version=world_version,
                    mind_commit_version=heroine_runtime.version,
                )
                connection.commit()
            except WorldMindStoreError:
                connection.rollback()
                raise
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("runtime projection rebuild failed") from error

        protagonist = _protagonist_from_payload(json.loads(protagonist_json))
        scene = _scene_from_payload(json.loads(scene_json))
        live_world = LiveWorldState(
            save_id=session.save_id,
            world_id=session.world_id,
            protagonist_id=session.protagonist_id,
            version=world_version,
            protagonist=protagonist,
            scene=scene,
            last_changed_game_time=_parse_game_time(changed_game_time),
        )
        return ProjectionRebuildResult(
            live_world=live_world,
            heroine_runtime=heroine_runtime,
        )

    def enqueue_post_reply_reconcile(
        self,
        session: RuntimeSessionIdentity,
        source_turn: ReconcileSourceTurn,
        game_time: datetime,
    ) -> ReconcileJob:
        return self._enqueue_reconcile_job(
            session=session,
            mode=POST_REPLY_WORLD_MIND_RECONCILE,
            dedupe_key=(
                f"post_reply:{session.save_id}:{session.active_character_id}:"
                f"{source_turn.request_id}"
            ),
            required_before_next_turn=True,
            source_request_id=source_turn.request_id,
            source_turn=source_turn,
            elapsed_runtime_seconds=0.0,
            missed_intervals=0,
            game_time=game_time,
        )

    def enqueue_periodic_reconcile(
        self,
        session: RuntimeSessionIdentity,
        game_time: datetime,
        *,
        interval_seconds: float,
    ) -> ReconcileJob:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        recorded_at = _recorded_time_text(self._recorded_clock())
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT * FROM world_mind_reconcile_jobs
                    WHERE save_id = ? AND character_id = ?
                      AND mode = ? AND state = 'pending'
                    ORDER BY created_at LIMIT 1
                    """,
                    (
                        session.save_id,
                        session.active_character_id,
                        FIVE_MINUTE_WORLD_MIND_RECONCILE,
                    ),
                ).fetchone()
                if row is not None:
                    connection.execute(
                        """
                        UPDATE world_mind_reconcile_jobs
                        SET elapsed_runtime_seconds = elapsed_runtime_seconds + ?,
                            missed_intervals = missed_intervals + 1,
                            updated_at = ?
                        WHERE job_id = ? AND state = 'pending'
                        """,
                        (interval_seconds, recorded_at, str(row["job_id"])),
                    )
                    row = connection.execute(
                        "SELECT * FROM world_mind_reconcile_jobs WHERE job_id = ?",
                        (str(row["job_id"]),),
                    ).fetchone()
                    connection.commit()
                    return _reconcile_job_from_row(row)
                job_id = self._id_factory()
                dedupe_key = (
                    f"periodic:{session.save_id}:{session.active_character_id}:"
                    f"{job_id}"
                )
                self._insert_reconcile_job(
                    connection,
                    job_id=job_id,
                    dedupe_key=dedupe_key,
                    session=session,
                    mode=FIVE_MINUTE_WORLD_MIND_RECONCILE,
                    required_before_next_turn=False,
                    source_request_id=None,
                    source_turn=None,
                    elapsed_runtime_seconds=interval_seconds,
                    missed_intervals=0,
                    game_time=game_time,
                    recorded_at=recorded_at,
                )
                row = connection.execute(
                    "SELECT * FROM world_mind_reconcile_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                connection.commit()
                return _reconcile_job_from_row(row)
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("periodic reconcile enqueue failed") from error

    def _enqueue_reconcile_job(
        self,
        *,
        session: RuntimeSessionIdentity,
        mode: str,
        dedupe_key: str,
        required_before_next_turn: bool,
        source_request_id: str | None,
        source_turn: ReconcileSourceTurn | None,
        elapsed_runtime_seconds: float,
        missed_intervals: int,
        game_time: datetime,
    ) -> ReconcileJob:
        recorded_at = _recorded_time_text(self._recorded_clock())
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM world_mind_reconcile_jobs WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
                if row is None:
                    job_id = self._id_factory()
                    self._insert_reconcile_job(
                        connection,
                        job_id=job_id,
                        dedupe_key=dedupe_key,
                        session=session,
                        mode=mode,
                        required_before_next_turn=required_before_next_turn,
                        source_request_id=source_request_id,
                        source_turn=source_turn,
                        elapsed_runtime_seconds=elapsed_runtime_seconds,
                        missed_intervals=missed_intervals,
                        game_time=game_time,
                        recorded_at=recorded_at,
                    )
                    row = connection.execute(
                        "SELECT * FROM world_mind_reconcile_jobs WHERE job_id = ?",
                        (job_id,),
                    ).fetchone()
                connection.commit()
                return _reconcile_job_from_row(row)
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("reconcile enqueue failed") from error

    @staticmethod
    def _insert_reconcile_job(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        dedupe_key: str,
        session: RuntimeSessionIdentity,
        mode: str,
        required_before_next_turn: bool,
        source_request_id: str | None,
        source_turn: ReconcileSourceTurn | None,
        elapsed_runtime_seconds: float,
        missed_intervals: int,
        game_time: datetime,
        recorded_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO world_mind_reconcile_jobs(
                job_id, dedupe_key, save_id, world_id, protagonist_id,
                character_id, conversation_id, mode,
                required_before_next_turn, source_request_id, source_turn_json,
                elapsed_runtime_seconds, missed_intervals, state, attempt,
                failure_code, created_game_time, available_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0,
                      NULL, ?, ?, ?, ?)
            """,
            (
                job_id,
                dedupe_key,
                session.save_id,
                session.world_id,
                session.protagonist_id,
                session.active_character_id,
                session.conversation_id,
                mode,
                int(required_before_next_turn),
                source_request_id,
                None if source_turn is None else _json(_source_turn_payload(source_turn)),
                elapsed_runtime_seconds,
                missed_intervals,
                _game_time_text(game_time),
                recorded_at,
                recorded_at,
                recorded_at,
            ),
        )

    def recover_reconcile_jobs(self) -> int:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE world_mind_reconcile_jobs
                SET state = 'pending', failure_code = 'interrupted',
                    available_at = ?, updated_at = ?
                WHERE state = 'running'
                """,
                (
                    _recorded_time_text(self._recorded_clock()),
                    _recorded_time_text(self._recorded_clock()),
                ),
            )
            return int(cursor.rowcount)

    def claim_next_reconcile_job(
        self,
        session: RuntimeSessionIdentity,
        *,
        required_only: bool = False,
    ) -> ReconcileJob | None:
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                required_clause = "AND required_before_next_turn = 1" if required_only else ""
                recorded_at = _recorded_time_text(self._recorded_clock())
                row = connection.execute(
                    f"""
                    SELECT * FROM world_mind_reconcile_jobs
                    WHERE save_id = ? AND character_id = ? AND state = 'pending'
                      AND available_at <= ?
                    {required_clause}
                    ORDER BY required_before_next_turn DESC, created_at, job_id
                    LIMIT 1
                    """,
                    (session.save_id, session.active_character_id, recorded_at),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    """
                    UPDATE world_mind_reconcile_jobs
                    SET state = 'running', attempt = attempt + 1,
                        failure_code = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'pending'
                    """,
                    (
                        recorded_at,
                        str(row["job_id"]),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM world_mind_reconcile_jobs WHERE job_id = ?",
                    (str(row["job_id"]),),
                ).fetchone()
                connection.commit()
                return _reconcile_job_from_row(row)
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("reconcile claim failed") from error

    def requeue_reconcile_job(
        self,
        job_id: str,
        failure_code: str,
        *,
        retry_delay_seconds: float = 0.25,
    ) -> None:
        available_at = self._retry_available_at(retry_delay_seconds)
        with self._lock:
            self._connection.execute(
                """
                UPDATE world_mind_reconcile_jobs
                SET state = 'pending', failure_code = ?, available_at = ?, updated_at = ?
                WHERE job_id = ? AND state = 'running'
                """,
                (
                    failure_code,
                    available_at,
                    _recorded_time_text(self._recorded_clock()),
                    job_id,
                ),
            )

    def fail_reconcile_job(
        self,
        job: ReconcileJob,
        failure_code: str,
        *,
        retryable: bool,
        max_attempts: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        state = "pending" if retryable and job.attempt < max_attempts else "failed"
        available_at = self._retry_available_at(
            retry_delay_seconds if state == "pending" else 0.0
        )
        with self._lock:
            self._connection.execute(
                """
                UPDATE world_mind_reconcile_jobs
                SET state = ?, failure_code = ?, available_at = ?, updated_at = ?
                WHERE job_id = ? AND state = 'running'
                """,
                (
                    state,
                    failure_code,
                    available_at,
                    _recorded_time_text(self._recorded_clock()),
                    job.job_id,
                ),
            )

    def has_required_reconcile_jobs(self, session: RuntimeSessionIdentity) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM world_mind_reconcile_jobs
                WHERE save_id = ? AND character_id = ?
                  AND required_before_next_turn = 1
                  AND state IN ('pending', 'running')
                LIMIT 1
                """,
                (session.save_id, session.active_character_id),
            ).fetchone()
            return row is not None

    def supersede_older_pending_required_jobs(
        self, session: RuntimeSessionIdentity
    ) -> int:
        with self._lock:
            connection = self._connection
            rows = connection.execute(
                """
                SELECT job_id FROM world_mind_reconcile_jobs
                WHERE save_id = ? AND character_id = ?
                  AND required_before_next_turn = 1 AND state = 'pending'
                ORDER BY created_at DESC, job_id DESC
                """,
                (session.save_id, session.active_character_id),
            ).fetchall()
            obsolete = [str(row["job_id"]) for row in rows[1:]]
            if not obsolete:
                return 0
            placeholders = ",".join("?" for _ in obsolete)
            connection.execute(
                f"""
                UPDATE world_mind_reconcile_jobs
                SET state = 'failed', failure_code = ?, updated_at = ?
                WHERE job_id IN ({placeholders}) AND state = 'pending'
                """,
                (
                    "superseded_by_newer_required_job",
                    _recorded_time_text(self._recorded_clock()),
                    *obsolete,
                ),
            )
            connection.commit()
            return len(obsolete)

    def has_pending_reconcile_jobs(self, session: RuntimeSessionIdentity) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM world_mind_reconcile_jobs
                WHERE save_id = ? AND character_id = ? AND state = 'pending'
                LIMIT 1
                """,
                (session.save_id, session.active_character_id),
            ).fetchone()
        return row is not None

    def next_reconcile_ready_delay(
        self, session: RuntimeSessionIdentity
    ) -> float | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT MIN(available_at) AS available_at
                FROM world_mind_reconcile_jobs
                WHERE save_id = ? AND character_id = ? AND state = 'pending'
                """,
                (session.save_id, session.active_character_id),
            ).fetchone()
        if row is None or row["available_at"] is None:
            return None
        return max(
            0.0,
            (
                _parse_recorded_time(str(row["available_at"]))
                - self._recorded_clock().astimezone(timezone.utc)
            ).total_seconds(),
        )

    @staticmethod
    def _insert_r1_memory_job(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        dedupe_key: str,
        session: RuntimeSessionIdentity,
        source_request_id: str,
        user_event_id: str,
        assistant_event_id: str,
        game_time: datetime,
        recorded_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO world_mind_r1_memory_jobs(
                job_id, dedupe_key, save_id, world_id, protagonist_id,
                character_id, conversation_id, source_request_id,
                user_event_id, assistant_event_id, state, attempt,
                failure_code, created_game_time, available_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0,
                      NULL, ?, ?, ?, ?)
            """,
            (
                job_id,
                dedupe_key,
                session.save_id,
                session.world_id,
                session.protagonist_id,
                session.active_character_id,
                session.conversation_id,
                source_request_id,
                user_event_id,
                assistant_event_id,
                _game_time_text(game_time),
                recorded_at,
                recorded_at,
                recorded_at,
            ),
        )

    def recover_r1_memory_jobs(self) -> int:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE world_mind_r1_memory_jobs
                SET state = 'pending', failure_code = 'interrupted',
                    available_at = ?, updated_at = ?
                WHERE state = 'running'
                """,
                (
                    _recorded_time_text(self._recorded_clock()),
                    _recorded_time_text(self._recorded_clock()),
                ),
            )
            return int(cursor.rowcount)

    def claim_next_r1_memory_job(
        self,
        session: RuntimeSessionIdentity,
    ) -> R1MemoryJob | None:
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                recorded_at = _recorded_time_text(self._recorded_clock())
                row = connection.execute(
                    """
                    SELECT * FROM world_mind_r1_memory_jobs
                    WHERE save_id = ? AND world_id = ?
                      AND protagonist_id = ? AND character_id = ?
                      AND state = 'pending'
                      AND available_at <= ?
                    ORDER BY created_at, job_id
                    LIMIT 1
                    """,
                    (
                        session.save_id,
                        session.world_id,
                        session.protagonist_id,
                        session.active_character_id,
                        recorded_at,
                    ),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute(
                    """
                    UPDATE world_mind_r1_memory_jobs
                    SET state = 'running', attempt = attempt + 1,
                        failure_code = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'pending'
                    """,
                    (
                        recorded_at,
                        str(row["job_id"]),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM world_mind_r1_memory_jobs WHERE job_id = ?",
                    (str(row["job_id"]),),
                ).fetchone()
                connection.commit()
                return _r1_memory_job_from_row(row)
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("R1 memory job claim failed") from error

    def requeue_r1_memory_job(
        self,
        job_id: str,
        failure_code: str,
        *,
        retry_delay_seconds: float = 0.25,
    ) -> None:
        available_at = self._retry_available_at(retry_delay_seconds)
        with self._lock:
            self._connection.execute(
                """
                UPDATE world_mind_r1_memory_jobs
                SET state = 'pending', failure_code = ?, available_at = ?, updated_at = ?
                WHERE job_id = ? AND state = 'running'
                """,
                (
                    failure_code,
                    available_at,
                    _recorded_time_text(self._recorded_clock()),
                    job_id,
                ),
            )

    def fail_r1_memory_job(
        self,
        job: R1MemoryJob,
        failure_code: str,
        *,
        retryable: bool,
        max_attempts: int = 3,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        state = "pending" if retryable and job.attempt < max_attempts else "failed"
        available_at = self._retry_available_at(
            retry_delay_seconds if state == "pending" else 0.0
        )
        with self._lock:
            self._connection.execute(
                """
                UPDATE world_mind_r1_memory_jobs
                SET state = ?, failure_code = ?, available_at = ?, updated_at = ?
                WHERE job_id = ? AND state = 'running'
                """,
                (
                    state,
                    failure_code,
                    available_at,
                    _recorded_time_text(self._recorded_clock()),
                    job.job_id,
                ),
            )

    def complete_r1_memory_job(self, job_id: str) -> None:
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE world_mind_r1_memory_jobs
                SET state = 'completed', failure_code = NULL, updated_at = ?
                WHERE job_id = ? AND state = 'running'
                """,
                (_recorded_time_text(self._recorded_clock()), job_id),
            )
            if cursor.rowcount != 1:
                raise WorldMindStoreError("R1 memory job completion failed")

    def validate_r1_memory_job_sources(self, job: R1MemoryJob) -> None:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_id, save_id, world_id, protagonist_id,
                       character_id, request_id
                FROM world_mind_events
                WHERE event_id IN (?, ?)
                """,
                (job.user_event_id, job.assistant_event_id),
            ).fetchall()
        if len(rows) != 2:
            raise WorldMindStoreError("R1 memory job source event is missing")
        expected_ids = {job.user_event_id, job.assistant_event_id}
        if {str(row["event_id"]) for row in rows} != expected_ids:
            raise WorldMindStoreError("R1 memory job source event identity mismatch")
        if any(
            str(row["save_id"]) != job.session.save_id
            or str(row["world_id"]) != job.session.world_id
            or str(row["protagonist_id"]) != job.session.protagonist_id
            or str(row["character_id"]) != job.session.active_character_id
            or str(row["request_id"]) != job.source_request_id
            for row in rows
        ):
            raise WorldMindStoreError("R1 memory job source escaped its heroine turn")

    def has_pending_r1_memory_jobs(self, session: RuntimeSessionIdentity) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM world_mind_r1_memory_jobs
                WHERE save_id = ? AND world_id = ?
                  AND protagonist_id = ? AND character_id = ?
                  AND state = 'pending'
                LIMIT 1
                """,
                (
                    session.save_id,
                    session.world_id,
                    session.protagonist_id,
                    session.active_character_id,
                ),
            ).fetchone()
        return row is not None

    def next_r1_memory_ready_delay(
        self, session: RuntimeSessionIdentity
    ) -> float | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT MIN(available_at) AS available_at
                FROM world_mind_r1_memory_jobs
                WHERE save_id = ? AND world_id = ?
                  AND protagonist_id = ? AND character_id = ?
                  AND state = 'pending'
                """,
                (
                    session.save_id,
                    session.world_id,
                    session.protagonist_id,
                    session.active_character_id,
                ),
            ).fetchone()
        if row is None or row["available_at"] is None:
            return None
        return max(
            0.0,
            (
                _parse_recorded_time(str(row["available_at"]))
                - self._recorded_clock().astimezone(timezone.utc)
            ).total_seconds(),
        )

    def count_r1_memory_jobs(
        self,
        save_id: str,
        *,
        character_id: str | None = None,
        state: str | None = None,
    ) -> int:
        query = "SELECT COUNT(*) FROM world_mind_r1_memory_jobs WHERE save_id = ?"
        parameters: list[object] = [save_id]
        if character_id is not None:
            query += " AND character_id = ?"
            parameters.append(character_id)
        if state is not None:
            query += " AND state = ?"
            parameters.append(state)
        with self._lock:
            return int(self._connection.execute(query, parameters).fetchone()[0])

    def r1_memory_queue_snapshot(self, save_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM world_mind_r1_memory_jobs
                WHERE save_id = ? GROUP BY state
                """,
                (save_id,),
            ).fetchall()
        metrics = {str(row["state"]): int(row["count"]) for row in rows}
        return {
            "pending": metrics.get("pending", 0),
            "running": metrics.get("running", 0),
            "completed": metrics.get("completed", 0),
            "failed": metrics.get("failed", 0),
        }

    def load_reconcile_checkpoint(
        self,
        session: RuntimeSessionIdentity,
    ) -> ReconcileCheckpoint | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM world_mind_reconcile_checkpoints
                WHERE save_id = ? AND character_id = ?
                """,
                (session.save_id, session.active_character_id),
            ).fetchone()
        if row is None:
            return None
        if str(row["world_id"]) != session.world_id:
            raise WorldMindStoreError("reconcile checkpoint world mismatch")
        return ReconcileCheckpoint(
            save_id=str(row["save_id"]),
            world_id=str(row["world_id"]),
            character_id=str(row["character_id"]),
            last_world_version=int(row["last_world_version"]),
            last_game_time=_parse_game_time(str(row["last_game_time"])),
            last_job_id=(str(row["last_job_id"]) if row["last_job_id"] else None),
        )

    def list_world_event_deltas(
        self,
        session: RuntimeSessionIdentity,
        *,
        after_version: int,
        through_version: int,
    ) -> tuple[WorldEventDelta, ...]:
        if after_version < 0 or through_version < after_version:
            raise ValueError("invalid world event delta version range")
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM world_event_deltas
                WHERE save_id = ? AND world_id = ?
                  AND to_version > ? AND to_version <= ?
                ORDER BY to_version
                """,
                (session.save_id, session.world_id, after_version, through_version),
            ).fetchall()
        return tuple(_world_event_delta_from_row(row) for row in rows)

    def commit_reconcile(
        self,
        job: ReconcileJob,
        snapshot: ReconcileWorldSnapshot,
        previous_runtime: HeroineRuntime,
        approved_runtime: HeroineRuntime,
        result: ReconcileMindResult,
        review: ContinuityReviewResult,
        model_identity: dict[str, Any],
    ) -> HeroineRuntime:
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                job_row = connection.execute(
                    "SELECT * FROM world_mind_reconcile_jobs WHERE job_id = ?",
                    (job.job_id,),
                ).fetchone()
                if job_row is None or str(job_row["state"]) != "running":
                    raise WorldMindStoreError("reconcile job is not running")
                current_row = connection.execute(
                    """
                    SELECT * FROM heroine_runtime_current
                    WHERE save_id = ? AND character_id = ?
                    """,
                    (job.session.save_id, job.session.active_character_id),
                ).fetchone()
                if current_row is None:
                    raise MindStateConflictError("heroine runtime is missing")
                current = _heroine_runtime_from_row(current_row)
                if current.version != previous_runtime.version:
                    raise MindStateConflictError("heroine runtime changed during reconcile")
                self._require_heroine_identity(approved_runtime, job.session)
                if approved_runtime.version not in {current.version, current.version + 1}:
                    raise MindStateConflictError("invalid reconciled mind version")

                recorded_at = _recorded_time_text(self._recorded_clock())
                game_time_text = _game_time_text(snapshot.captured_game_time)
                committed_operation = (
                    "update" if approved_runtime.version == current.version + 1 else "keep"
                )
                if committed_operation == "update":
                    transition_id = self._id_factory()
                    runtime_json = _json(_heroine_runtime_payload(approved_runtime))
                    connection.execute(
                        """
                        INSERT INTO heroine_mind_transitions(
                            transition_id, save_id, world_id, protagonist_id,
                            character_id, request_id, snapshot_id, from_version,
                            to_version, evidence_refs_json, payload_json,
                            game_time, recorded_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            transition_id,
                            job.session.save_id,
                            job.session.world_id,
                            job.session.protagonist_id,
                            job.session.active_character_id,
                            job.job_id,
                            snapshot.snapshot_id,
                            current.version,
                            approved_runtime.version,
                            _json(list(approved_runtime.evidence_refs)),
                            runtime_json,
                            game_time_text,
                            recorded_at,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE heroine_runtime_current
                        SET version = ?, payload_json = ?, last_transition_id = ?
                        WHERE save_id = ? AND character_id = ? AND version = ?
                        """,
                        (
                            approved_runtime.version,
                            runtime_json,
                            transition_id,
                            job.session.save_id,
                            job.session.active_character_id,
                            current.version,
                        ),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise MindStateConflictError("reconcile compare-and-set failed")
                    self._upsert_save_versions(
                        connection,
                        job.session.save_id,
                        job.session.world_id,
                        mind_commit_version=approved_runtime.version,
                    )

                connection.execute(
                    """
                    INSERT INTO world_mind_reconcile_decisions(
                        decision_id, job_id, save_id, world_id, protagonist_id,
                        character_id, mode, snapshot_id, from_mind_version,
                        to_mind_version, operation, reconcile_result_json,
                        continuity_review_json, model_identity_json,
                        committed_game_time, committed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._id_factory(),
                        job.job_id,
                        job.session.save_id,
                        job.session.world_id,
                        job.session.protagonist_id,
                        job.session.active_character_id,
                        job.mode,
                        snapshot.snapshot_id,
                        current.version,
                        approved_runtime.version,
                        committed_operation,
                        _json(reconcile_result_record(result)),
                        _json(continuity_result_record(review)),
                        _json(model_identity),
                        game_time_text,
                        recorded_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO world_mind_reconcile_checkpoints(
                        save_id, world_id, character_id, last_world_version,
                        last_game_time, last_job_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(save_id, character_id) DO UPDATE SET
                        world_id = excluded.world_id,
                        last_world_version = excluded.last_world_version,
                        last_game_time = excluded.last_game_time,
                        last_job_id = excluded.last_job_id,
                        updated_at = excluded.updated_at
                    """,
                    (
                        job.session.save_id,
                        job.session.world_id,
                        job.session.active_character_id,
                        snapshot.live_world_version,
                        game_time_text,
                        job.job_id,
                        recorded_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE world_mind_reconcile_jobs
                    SET state = 'completed', failure_code = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'running'
                    """,
                    (recorded_at, job.job_id),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise WorldMindStoreError("reconcile job completion failed")
                if self._before_reconcile_commit is not None:
                    self._before_reconcile_commit()
                connection.commit()
                return approved_runtime
            except (MindStateConflictError, WorldMindStoreError):
                connection.rollback()
                raise
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("atomic reconcile commit failed") from error

    def count_reconcile_jobs(self, save_id: str, *, state: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM world_mind_reconcile_jobs WHERE save_id = ?"
        parameters: tuple[object, ...] = (save_id,)
        if state is not None:
            query += " AND state = ?"
            parameters += (state,)
        with self._lock:
            return int(self._connection.execute(query, parameters).fetchone()[0])

    def count_reconcile_decisions(self, save_id: str) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM world_mind_reconcile_decisions WHERE save_id = ?",
                (save_id,),
            ).fetchone()[0])

    def reconcile_queue_snapshot(self, save_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM world_mind_reconcile_jobs
                WHERE save_id = ? GROUP BY state
                """,
                (save_id,),
            ).fetchall()
            metrics = {str(row["state"]): int(row["count"]) for row in rows}
            coalesced = self._connection.execute(
                """
                SELECT COALESCE(SUM(missed_intervals), 0)
                FROM world_mind_reconcile_jobs WHERE save_id = ?
                """,
                (save_id,),
            ).fetchone()[0]
        return {
            "pending": metrics.get("pending", 0),
            "running": metrics.get("running", 0),
            "completed": metrics.get("completed", 0),
            "failed": metrics.get("failed", 0),
            "coalesced_intervals": int(coalesced),
        }

    def consistency_audit(self, save_id: str) -> dict[str, int]:
        with self._lock:
            connection = self._connection
            orphan_turns = int(connection.execute(
                """
                SELECT COUNT(*) FROM turn_transactions AS tx
                LEFT JOIN world_mind_events AS user_event
                  ON user_event.event_id = tx.user_event_id
                LEFT JOIN world_mind_events AS assistant_event
                  ON assistant_event.event_id = tx.assistant_event_id
                WHERE tx.save_id = ?
                  AND (user_event.event_id IS NULL OR assistant_event.event_id IS NULL)
                """,
                (save_id,),
            ).fetchone()[0])
            orphan_events = int(connection.execute(
                """
                SELECT COUNT(*) FROM world_mind_events AS event
                LEFT JOIN turn_transactions AS tx
                  ON tx.save_id = event.save_id AND tx.request_id = event.request_id
                WHERE event.save_id = ? AND tx.request_id IS NULL
                """,
                (save_id,),
            ).fetchone()[0])
            duplicate_requests = int(connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT request_id FROM turn_transactions
                    WHERE save_id = ? GROUP BY request_id HAVING COUNT(*) > 1
                )
                """,
                (save_id,),
            ).fetchone()[0])
            event_count_mismatch = int(connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT tx.request_id, COUNT(event.event_id) AS event_count
                    FROM turn_transactions AS tx
                    LEFT JOIN world_mind_events AS event
                      ON event.save_id = tx.save_id
                     AND event.request_id = tx.request_id
                    WHERE tx.save_id = ?
                    GROUP BY tx.request_id HAVING event_count != 2
                )
                """,
                (save_id,),
            ).fetchone()[0])
            dangling_decisions = int(connection.execute(
                """
                SELECT COUNT(*) FROM turn_model_decisions AS decision
                LEFT JOIN turn_transactions AS tx
                  ON tx.save_id = decision.save_id
                 AND tx.request_id = decision.request_id
                WHERE decision.save_id = ? AND tx.request_id IS NULL
                """,
                (save_id,),
            ).fetchone()[0])
        return {
            "orphan_turns": orphan_turns,
            "orphan_events": orphan_events,
            "duplicate_requests": duplicate_requests,
            "event_count_mismatch": event_count_mismatch,
            "dangling_model_decisions": dangling_decisions,
        }

    def load_reconcile_decision(self, job_id: str) -> ReconcileDecision | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM world_mind_reconcile_decisions WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return ReconcileDecision(
            job_id=str(row["job_id"]),
            mode=str(row["mode"]),
            snapshot_id=str(row["snapshot_id"]),
            from_mind_version=int(row["from_mind_version"]),
            to_mind_version=int(row["to_mind_version"]),
            operation=str(row["operation"]),
            reconcile_result=json.loads(str(row["reconcile_result_json"])),
            continuity_review=json.loads(str(row["continuity_review_json"])),
            committed_game_time=_parse_game_time(str(row["committed_game_time"])),
        )

    def find_committed_turn(self, request: TurnRequest) -> CommittedTurn | None:
        request_sha256 = _request_sha256(request)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM turn_transactions
                WHERE save_id = ? AND request_id = ?
                """,
                (request.session.save_id, request.request_id),
            ).fetchone()
        if row is None:
            return None
        if str(row["request_sha256"]) != request_sha256:
            raise TurnRequestConflictError(
                "request_id already belongs to a different V6 turn"
            )
        return _committed_turn_from_row(row)

    def list_recent_dialogue(
        self,
        session: RuntimeSessionIdentity,
        *,
        limit: int = 6,
    ) -> tuple[tuple[str, str], ...]:
        if type(limit) is not int or limit < 0 or limit > 20:
            raise ValueError("recent dialogue limit must be between 0 and 20")
        if limit == 0:
            return ()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT actor, text
                FROM world_mind_events
                WHERE save_id = ? AND world_id = ? AND protagonist_id = ?
                  AND character_id = ? AND conversation_id = ?
                ORDER BY sequence_no DESC
                LIMIT ?
                """,
                (
                    session.save_id,
                    session.world_id,
                    session.protagonist_id,
                    session.active_character_id,
                    session.conversation_id,
                    limit,
                ),
            ).fetchall()
        return tuple(
            (str(row["actor"]), str(row["text"])) for row in reversed(rows)
        )

    def commit_turn(
        self,
        request: TurnRequest,
        snapshot: TurnWorldSnapshot,
        approved_runtime: HeroineRuntime,
        mind_result: MindAdvanceResult,
        continuity_review: ContinuityReviewResult,
        reply_result: GameReplyResult,
        model_identity: dict[str, Any],
        *,
        enqueue_r1_memory: bool = False,
    ) -> CommittedTurn:
        request_sha256 = _request_sha256(request)
        with self._lock:
            connection = self._connection
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    """
                    SELECT * FROM turn_transactions
                    WHERE save_id = ? AND request_id = ?
                    """,
                    (request.session.save_id, request.request_id),
                ).fetchone()
                if existing is not None:
                    if str(existing["request_sha256"]) != request_sha256:
                        raise TurnRequestConflictError(
                            "request_id already belongs to a different V6 turn"
                        )
                    connection.commit()
                    return _committed_turn_from_row(existing)

                current_row = connection.execute(
                    """
                    SELECT * FROM heroine_runtime_current
                    WHERE save_id = ? AND character_id = ?
                    """,
                    (request.session.save_id,
                     request.session.active_character_id),
                ).fetchone()
                if current_row is None:
                    raise MindStateConflictError("heroine runtime is missing")
                current = _heroine_runtime_from_row(current_row)
                if current.version != snapshot.mind_state_version:
                    raise MindStateConflictError("heroine runtime changed after snapshot")
                if approved_runtime.version != current.version + 1:
                    raise MindStateConflictError("approved mind version is not next")
                self._require_heroine_identity(approved_runtime, request.session)

                next_sequence = int(connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence_no), 0) + 1
                    FROM world_mind_events WHERE save_id = ?
                    """,
                    (request.session.save_id,),
                ).fetchone()[0])
                user_event_id = (
                    snapshot.protagonist_utterance_event_id or self._id_factory()
                )
                assistant_event_id = self._id_factory()
                transition_id = self._id_factory()
                transaction_id = self._id_factory()
                game_time_text = _game_time_text(snapshot.captured_game_time)
                recorded_at = _recorded_time_text(self._recorded_clock())
                common = (
                    request.session.save_id,
                    request.session.world_id,
                    request.session.protagonist_id,
                    request.session.active_character_id,
                    request.request_id,
                    request.session.conversation_id,
                )
                connection.execute(
                    """
                    INSERT INTO world_mind_events(
                        event_id, save_id, world_id, protagonist_id, character_id,
                        request_id, conversation_id, sequence_no, game_time,
                        actor, event_type, text, snapshot_id,
                        causation_event_id, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'protagonist',
                              'utterance', ?, ?, NULL, ?)
                    """,
                    (user_event_id, *common, next_sequence, game_time_text,
                     request.text, snapshot.snapshot_id, recorded_at),
                )
                connection.execute(
                    """
                    INSERT INTO world_mind_events(
                        event_id, save_id, world_id, protagonist_id, character_id,
                        request_id, conversation_id, sequence_no, game_time,
                        actor, event_type, text, snapshot_id,
                        causation_event_id, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'heroine',
                              'reply', ?, ?, ?, ?)
                    """,
                    (assistant_event_id, *common, next_sequence + 1,
                     game_time_text, reply_result.text, snapshot.snapshot_id,
                     user_event_id, recorded_at),
                )
                runtime_json = _json(_heroine_runtime_payload(approved_runtime))
                connection.execute(
                    """
                    INSERT INTO heroine_mind_transitions(
                        transition_id, save_id, world_id, protagonist_id,
                        character_id, request_id, snapshot_id, from_version,
                        to_version, evidence_refs_json, payload_json,
                        game_time, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (transition_id, request.session.save_id,
                     request.session.world_id, request.session.protagonist_id,
                     request.session.active_character_id, request.request_id,
                     snapshot.snapshot_id, current.version,
                     approved_runtime.version,
                     _json(list(approved_runtime.evidence_refs)), runtime_json,
                     game_time_text, recorded_at),
                )
                connection.execute(
                    """
                    UPDATE heroine_runtime_current
                    SET version = ?, payload_json = ?, last_transition_id = ?
                    WHERE save_id = ? AND character_id = ? AND version = ?
                    """,
                    (approved_runtime.version, runtime_json, transition_id,
                     request.session.save_id,
                     request.session.active_character_id, current.version),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise MindStateConflictError(
                        "heroine runtime compare-and-set failed"
                    )
                self._upsert_save_versions(
                    connection,
                    request.session.save_id,
                    request.session.world_id,
                    mind_commit_version=approved_runtime.version,
                )
                connection.execute(
                    """
                    INSERT INTO turn_transactions(
                        save_id, request_id, request_sha256, transaction_id,
                        world_id, protagonist_id, character_id, conversation_id,
                        snapshot_id, snapshot_json, approved_runtime_json,
                        user_event_id, assistant_event_id, reply_text,
                        committed_game_time, committed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (request.session.save_id, request.request_id,
                     request_sha256, transaction_id, request.session.world_id,
                     request.session.protagonist_id,
                     request.session.active_character_id,
                     request.session.conversation_id, snapshot.snapshot_id,
                     _json(_snapshot_payload(snapshot)), runtime_json,
                     user_event_id, assistant_event_id, reply_result.text,
                     game_time_text, recorded_at),
                )
                connection.execute(
                    """
                    INSERT INTO turn_model_decisions(
                        save_id, request_id, world_id, protagonist_id,
                        character_id, snapshot_id, mind_result_json,
                        continuity_review_json, game_reply_result_json,
                        model_identity_json, committed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.session.save_id,
                        request.request_id,
                        request.session.world_id,
                        request.session.protagonist_id,
                        request.session.active_character_id,
                        snapshot.snapshot_id,
                        _json(mind_result_record(mind_result)),
                        _json(continuity_result_record(continuity_review)),
                        _json(game_reply_result_record(reply_result)),
                        _json(model_identity),
                        recorded_at,
                    ),
                )
                source_turn = ReconcileSourceTurn(
                    request_id=request.request_id,
                    user_event_id=user_event_id,
                    assistant_event_id=assistant_event_id,
                    protagonist_utterance=request.text,
                    heroine_reply=reply_result.text,
                    approved_actions=mind_result.heroine_diegetic_actions,
                )
                self._insert_reconcile_job(
                    connection,
                    job_id=self._id_factory(),
                    dedupe_key=(
                        f"post_reply:{request.session.save_id}:"
                        f"{request.session.active_character_id}:{request.request_id}"
                    ),
                    session=request.session,
                    mode=POST_REPLY_WORLD_MIND_RECONCILE,
                    required_before_next_turn=True,
                    source_request_id=request.request_id,
                    source_turn=source_turn,
                    elapsed_runtime_seconds=0.0,
                    missed_intervals=0,
                    game_time=snapshot.captured_game_time,
                    recorded_at=recorded_at,
                )
                if enqueue_r1_memory:
                    self._insert_r1_memory_job(
                        connection,
                        job_id=self._id_factory(),
                        dedupe_key=(
                            f"r1:{request.session.save_id}:"
                            f"{request.session.active_character_id}:{request.request_id}"
                        ),
                        session=request.session,
                        source_request_id=request.request_id,
                        user_event_id=user_event_id,
                        assistant_event_id=assistant_event_id,
                        game_time=snapshot.captured_game_time,
                        recorded_at=recorded_at,
                    )
                if self._before_turn_commit is not None:
                    self._before_turn_commit()
                connection.commit()
            except (TurnRequestConflictError, MindStateConflictError):
                connection.rollback()
                raise
            except Exception as error:
                connection.rollback()
                raise WorldMindStoreError("atomic turn commit failed") from error
        return CommittedTurn(
            transaction_id=transaction_id,
            request_id=request.request_id,
            user_event_id=user_event_id,
            assistant_event_id=assistant_event_id,
            text=reply_result.text,
            committed_game_time=snapshot.captured_game_time,
            heroine_runtime=approved_runtime,
        )

    def count_turn_events(self, save_id: str) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM world_mind_events WHERE save_id = ?",
                (save_id,),
            ).fetchone()[0])

    def count_turn_transactions(self, save_id: str) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM turn_transactions WHERE save_id = ?",
                (save_id,),
            ).fetchone()[0])

    def count_model_decisions(self, save_id: str) -> int:
        with self._lock:
            return int(self._connection.execute(
                "SELECT COUNT(*) FROM turn_model_decisions WHERE save_id = ?",
                (save_id,),
            ).fetchone()[0])

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
        migration_dir = Path(__file__).parents[1] / "migrations"
        paths = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
        for path in paths:
            version = int(path.name.split("_", 1)[0])
            if version < 8:
                continue
            exists = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            if exists is not None:
                continue
            applied_at = _recorded_time_text(self._recorded_clock()).replace("'", "''")
            script = (
                "BEGIN IMMEDIATE;\n"
                + path.read_text(encoding="utf-8")
                + "\nINSERT INTO schema_migrations(version, applied_at) "
                + f"VALUES ({version}, '{applied_at}');\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.Error as error:
                if connection.in_transaction:
                    connection.rollback()
                raise WorldMindStoreError(f"migration {path.name} failed") from error

    @staticmethod
    def _require_live_identity(
        row: sqlite3.Row,
        session: RuntimeSessionIdentity,
    ) -> None:
        if str(row["world_id"]) != session.world_id:
            raise WorldMindStoreError("live world identity mismatch")
        if str(row["protagonist_id"]) != session.protagonist_id:
            raise WorldMindStoreError("live protagonist identity mismatch")

    @staticmethod
    def _require_heroine_identity(
        runtime: HeroineRuntime,
        session: RuntimeSessionIdentity,
    ) -> None:
        if runtime.save_id != session.save_id:
            raise WorldMindStoreError("heroine runtime save identity mismatch")
        if runtime.world_id != session.world_id:
            raise WorldMindStoreError("heroine runtime world identity mismatch")
        if runtime.character_id != session.active_character_id:
            raise WorldMindStoreError("heroine runtime character identity mismatch")
        if runtime.relationship.protagonist_id != session.protagonist_id:
            raise WorldMindStoreError("heroine runtime protagonist identity mismatch")

    @staticmethod
    def _live_world_from_rows(
        session: RuntimeSessionIdentity,
        protagonist_row: sqlite3.Row,
        scene_row: sqlite3.Row,
    ) -> LiveWorldState:
        protagonist = _protagonist_from_payload(
            json.loads(str(protagonist_row["payload_json"]))
        )
        scene = _scene_from_payload(json.loads(str(scene_row["payload_json"])))
        version = int(protagonist_row["version"])
        if version != int(scene_row["version"]):
            raise WorldMindStoreError("live world component versions do not match")
        return LiveWorldState(
            save_id=session.save_id,
            world_id=session.world_id,
            protagonist_id=session.protagonist_id,
            version=version,
            protagonist=protagonist,
            scene=scene,
            last_changed_game_time=_parse_game_time(
                str(protagonist_row["changed_game_time"])
            ),
        )

    def _upsert_save_versions(
        self,
        connection: sqlite3.Connection,
        save_id: str,
        world_id: str,
        *,
        live_world_version: int | None = None,
        mind_commit_version: int | None = None,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM save_runtime_versions WHERE save_id = ?",
            (save_id,),
        ).fetchone()
        if row is not None and str(row["world_id"]) != world_id:
            raise WorldMindStoreError("save runtime world identity mismatch")
        current_live = 0 if row is None else int(row["live_world_version"])
        current_mind = 0 if row is None else int(row["mind_commit_version"])
        connection.execute(
            """
            INSERT INTO save_runtime_versions(
                save_id, world_id, live_world_version,
                mind_commit_version, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(save_id) DO UPDATE SET
                live_world_version = excluded.live_world_version,
                mind_commit_version = excluded.mind_commit_version,
                updated_at = excluded.updated_at
            """,
            (save_id, world_id,
             current_live if live_world_version is None else live_world_version,
             current_mind if mind_commit_version is None else mind_commit_version,
            _recorded_time_text(self._recorded_clock())),
        )

    def _retry_available_at(self, delay_seconds: float) -> str:
        if (
            isinstance(delay_seconds, bool)
            or not isinstance(delay_seconds, (int, float))
            or delay_seconds < 0
            or not math.isfinite(delay_seconds)
        ):
            raise ValueError("retry delay must be a finite non-negative number")
        return _recorded_time_text(
            self._recorded_clock() + timedelta(seconds=float(delay_seconds))
        )


def _request_sha256(request: TurnRequest) -> str:
    payload = {
        "request_id": request.request_id,
        "save_id": request.session.save_id,
        "world_id": request.session.world_id,
        "protagonist_id": request.session.protagonist_id,
        "active_character_id": request.session.active_character_id,
        "conversation_id": request.session.conversation_id,
        "text": request.text,
        "source": request.source,
    }
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _protagonist_payload(value: ProtagonistLiveState) -> dict[str, Any]:
    return {
        "protagonist_id": value.protagonist_id,
        "location_id": value.location_id,
        "location_label": value.location_label,
        "activity": value.activity,
        "body_state": dict(value.body_state),
        "held_item_ids": list(value.held_item_ids),
    }


def _protagonist_from_payload(value: dict[str, Any]) -> ProtagonistLiveState:
    return ProtagonistLiveState(
        protagonist_id=value["protagonist_id"],
        location_id=value["location_id"],
        location_label=value["location_label"],
        activity=value["activity"],
        body_state=value["body_state"],
        held_item_ids=tuple(value["held_item_ids"]),
    )


def _scene_payload(value: ActiveSceneState) -> dict[str, Any]:
    return {
        "scene_id": value.scene_id,
        "location_label": value.location_label,
        "present_character_ids": list(value.present_character_ids),
        "item_states": dict(value.item_states),
    }


def _scene_from_payload(value: dict[str, Any]) -> ActiveSceneState:
    return ActiveSceneState(
        scene_id=value["scene_id"],
        location_label=value["location_label"],
        present_character_ids=tuple(value["present_character_ids"]),
        item_states=value["item_states"],
    )


def _heroine_runtime_payload(value: HeroineRuntime) -> dict[str, Any]:
    return {
        "save_id": value.save_id,
        "world_id": value.world_id,
        "character_id": value.character_id,
        "version": value.version,
        "living_mind": asdict(value.living_mind),
        "relationship": asdict(value.relationship),
        "evidence_refs": list(value.evidence_refs),
        "motive_state": dict(value.motive_state),
        "knowledge_state": dict(value.knowledge_state),
    }


def _heroine_runtime_from_payload(value: dict[str, Any]) -> HeroineRuntime:
    return HeroineRuntime(
        save_id=value["save_id"],
        world_id=value["world_id"],
        character_id=value["character_id"],
        version=int(value["version"]),
        living_mind=LivingMind(**value["living_mind"]),
        relationship=RelationshipState(**value["relationship"]),
        evidence_refs=tuple(value["evidence_refs"]),
        motive_state=value.get("motive_state", {}),
        knowledge_state=value.get("knowledge_state", {}),
    )


def _heroine_runtime_from_row(row: sqlite3.Row) -> HeroineRuntime:
    return _heroine_runtime_from_payload(json.loads(str(row["payload_json"])))


def _snapshot_payload(snapshot: TurnWorldSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "request_id": snapshot.request_id,
        "captured_game_time": _game_time_text(snapshot.captured_game_time),
        "live_world_version": snapshot.live_world_version,
        "mind_state_version": snapshot.mind_state_version,
        "protagonist": _protagonist_payload(snapshot.protagonist),
        "scene": _scene_payload(snapshot.scene),
        "heroine_runtime": _heroine_runtime_payload(snapshot.heroine_runtime),
        "protagonist_utterance": snapshot.protagonist_utterance,
        "protagonist_utterance_event_id": snapshot.protagonist_utterance_event_id,
        "selected_memory_frame": asdict(snapshot.selected_memory_frame),
    }


def _committed_turn_from_row(row: sqlite3.Row) -> CommittedTurn:
    return CommittedTurn(
        transaction_id=str(row["transaction_id"]),
        request_id=str(row["request_id"]),
        user_event_id=str(row["user_event_id"]),
        assistant_event_id=str(row["assistant_event_id"]),
        text=str(row["reply_text"]),
        committed_game_time=_parse_game_time(str(row["committed_game_time"])),
        heroine_runtime=_heroine_runtime_from_payload(
            json.loads(str(row["approved_runtime_json"]))
        ),
    )


def _source_turn_payload(value: ReconcileSourceTurn) -> dict[str, Any]:
    return {
        "request_id": value.request_id,
        "user_event_id": value.user_event_id,
        "assistant_event_id": value.assistant_event_id,
        "protagonist_utterance": value.protagonist_utterance,
        "heroine_reply": value.heroine_reply,
        "approved_actions": [
            {
                "description": action.description,
                "evidence_refs": list(action.evidence_refs),
            }
            for action in value.approved_actions
        ],
    }


def _source_turn_from_payload(value: dict[str, Any]) -> ReconcileSourceTurn:
    return ReconcileSourceTurn(
        request_id=value["request_id"],
        user_event_id=value["user_event_id"],
        assistant_event_id=value["assistant_event_id"],
        protagonist_utterance=value["protagonist_utterance"],
        heroine_reply=value["heroine_reply"],
        approved_actions=tuple(
            HeroineDiegeticAction(
                description=item["description"],
                evidence_refs=tuple(item["evidence_refs"]),
            )
            for item in value.get("approved_actions", [])
        ),
    )


def _reconcile_job_from_row(row: sqlite3.Row) -> ReconcileJob:
    source_turn_json = row["source_turn_json"]
    return ReconcileJob(
        job_id=str(row["job_id"]),
        dedupe_key=str(row["dedupe_key"]),
        session=RuntimeSessionIdentity(
            save_id=str(row["save_id"]),
            world_id=str(row["world_id"]),
            protagonist_id=str(row["protagonist_id"]),
            active_character_id=str(row["character_id"]),
            conversation_id=str(row["conversation_id"]),
        ),
        mode=str(row["mode"]),
        required_before_next_turn=bool(row["required_before_next_turn"]),
        source_request_id=(
            str(row["source_request_id"])
            if row["source_request_id"] is not None
            else None
        ),
        source_turn=(
            _source_turn_from_payload(json.loads(str(source_turn_json)))
            if source_turn_json is not None
            else None
        ),
        elapsed_runtime_seconds=float(row["elapsed_runtime_seconds"]),
        missed_intervals=int(row["missed_intervals"]),
        state=str(row["state"]),
        attempt=int(row["attempt"]),
        failure_code=(
            str(row["failure_code"]) if row["failure_code"] is not None else None
        ),
        created_game_time=_parse_game_time(str(row["created_game_time"])),
        available_at=_parse_recorded_time(str(row["available_at"])),
    )


def _r1_memory_job_from_row(row: sqlite3.Row) -> R1MemoryJob:
    return R1MemoryJob(
        job_id=str(row["job_id"]),
        dedupe_key=str(row["dedupe_key"]),
        session=RuntimeSessionIdentity(
            save_id=str(row["save_id"]),
            world_id=str(row["world_id"]),
            protagonist_id=str(row["protagonist_id"]),
            active_character_id=str(row["character_id"]),
            conversation_id=str(row["conversation_id"]),
        ),
        source_request_id=str(row["source_request_id"]),
        user_event_id=str(row["user_event_id"]),
        assistant_event_id=str(row["assistant_event_id"]),
        state=str(row["state"]),
        attempt=int(row["attempt"]),
        failure_code=(
            str(row["failure_code"]) if row["failure_code"] is not None else None
        ),
        created_game_time=_parse_game_time(str(row["created_game_time"])),
        available_at=_parse_recorded_time(str(row["available_at"])),
    )


def _world_event_delta_from_row(row: sqlite3.Row) -> WorldEventDelta:
    return WorldEventDelta(
        event_id=str(row["event_id"]),
        save_id=str(row["save_id"]),
        from_version=int(row["from_version"]),
        to_version=int(row["to_version"]),
        changed_fields=tuple(json.loads(str(row["changed_fields_json"]))),
        game_time=_parse_game_time(str(row["game_time"])),
    )


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _game_time_text(value: datetime) -> str:
    if value.tzinfo is not None:
        raise ValueError("game time must not use a real-world timezone")
    return value.isoformat(timespec="microseconds")


def _parse_game_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        raise WorldMindStoreError("stored game time has a real-world timezone")
    return parsed


def _recorded_time_text(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("recorded clock must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_recorded_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise WorldMindStoreError("stored recorded time lacks a timezone")
    return parsed.astimezone(timezone.utc)
