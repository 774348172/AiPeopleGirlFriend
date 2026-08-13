"""AppendSink：call/candidate/failure/gate decision 的逐条 durable 持久化
（《数据生成器v4设计》§7.5、§13）。

SQLite 实现，提供：
- 单写者（文件锁，Windows msvcrt / POSIX fcntl）。
- 原子追加（BEGIN IMMEDIATE 事务）。
- 事务级持久化（WAL + synchronous=NORMAL，等价 fsync 语义）。
- 幂等键去重（idempotency_key UNIQUE；同键同内容返回已有记录，同键异内容冲突）。
- 崩溃恢复（事务回滚 + 重启后进度一致）。
- read_progress(run_id) 供 engine resume 使用。

内存 sink 仅用于测试（文档 §7.5）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import IdempotencyConflictError, SinkLockedError
from .records import (
    CandidateRecordV4,
    FailureRecord,
    GateDecisionRecord,
    RunEvent,
    V4Record,
    record_from_dict,
)


@dataclass(frozen=True, slots=True)
class DurableAppendResult:
    record_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class RunProgress:
    run_id: str
    run_started: bool = False
    generation_completed: bool = False
    run_started_plan_id: str | None = None
    run_started_lock_hash: str | None = None
    completed_plan_ids: frozenset[str] = frozenset()
    failed_plan_ids: frozenset[str] = frozenset()
    # 大块 B（阶段 2 P0-4）：per-candidate 进度——(plan_id, candidate_no) 已完成集合，
    # resume 据此跳过已产出候选（0 重新请求），补生成缺失 candidate_no
    completed_candidate_keys: frozenset[tuple[str, int]] = frozenset()
    candidates: tuple[Any, ...] = ()
    failures: tuple[Any, ...] = ()
    gate_decisions: tuple[Any, ...] = ()

    @property
    def completed(self) -> set[str]:
        return set(self.completed_plan_ids)


def _idempotency_key(run_id: str, plan_id: str | None, attempt_no: int | None,
                     candidate_no: int | None, stage: str) -> str:
    return json.dumps(
        [run_id, plan_id, attempt_no, candidate_no, stage],
        ensure_ascii=False,
        separators=(",", ":"),
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

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return cls(path, handle)
        except OSError as error:
            handle.close()
            raise SinkLockedError(f"sink 已被占用: {path}") from error

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


class AppendSink:
    def __init__(
        self,
        database_path: Path,
        connection: sqlite3.Connection,
        file_lock: _DatabaseFileLock,
    ) -> None:
        self.database_path = database_path
        self._connection = connection
        self._file_lock = file_lock
        self._closed = False

    @classmethod
    def open(cls, database_path: Path) -> AppendSink:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_lock = _DatabaseFileLock.acquire(path.with_suffix(path.suffix + ".lock"))
        try:
            connection = sqlite3.connect(path, isolation_level=None, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            sink = cls(path, connection, file_lock)
            sink._apply_migrations()
            return sink
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

    def __enter__(self) -> AppendSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ───────────────────────── 追加 ─────────────────────────

    def append(
        self,
        record: V4Record,
        idempotency_key: str,
    ) -> DurableAppendResult:
        payload = json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT payload_json FROM v4_records WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["payload_json"] != payload:
                    raise IdempotencyConflictError(
                        f"幂等键 {idempotency_key} 已存在且内容不同"
                    )
                connection.commit()
                return DurableAppendResult(record.header.record_id, False)

            try:
                connection.execute(
                    """
                    INSERT INTO v4_records (
                        record_type, record_id, idempotency_key,
                        run_id, plan_id, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.header.record_type,
                        record.header.record_id,
                        idempotency_key,
                        record.header.run_id,
                        record.header.plan_id,
                        payload,
                        record.header.created_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise IdempotencyConflictError(
                    f"record_id 冲突或幂等键重复: {error}"
                ) from error
            connection.commit()
            return DurableAppendResult(record.header.record_id, True)
        except Exception:
            connection.rollback()
            raise

    # ───────────────────────── 查询 ─────────────────────────

    def read_progress(self, run_id: str) -> RunProgress:
        rows = self._connection.execute(
            "SELECT payload_json FROM v4_records WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        candidates: list[CandidateRecordV4] = []
        failures: list[FailureRecord] = []
        gates: list[GateDecisionRecord] = []
        run_started = generation_completed = False
        run_started_plan_id: str | None = None
        run_started_lock_hash: str | None = None
        completed: set[str] = set()
        failed: set[str] = set()
        completed_candidates: set[tuple[str, int]] = set()
        for row in rows:
            record = record_from_dict(json.loads(row["payload_json"]))
            if isinstance(record, RunEvent):
                if record.event_name == "run_started":
                    run_started = True
                    run_started_plan_id = record.header.plan_id
                    run_started_lock_hash = record.header.package_lock_hash
                elif record.event_name == "generation_completed":
                    generation_completed = True
            elif isinstance(record, CandidateRecordV4):
                candidates.append(record)
                if record.header.plan_id:
                    completed.add(record.header.plan_id)
                    completed_candidates.add(
                        (record.header.plan_id, int(record.candidate_no))
                    )
            elif isinstance(record, FailureRecord):
                failures.append(record)
                if record.header.plan_id:
                    failed.add(record.header.plan_id)
            elif isinstance(record, GateDecisionRecord):
                gates.append(record)
        return RunProgress(
            run_id=run_id,
            run_started=run_started,
            generation_completed=generation_completed,
            run_started_plan_id=run_started_plan_id,
            run_started_lock_hash=run_started_lock_hash,
            completed_plan_ids=frozenset(completed),
            failed_plan_ids=frozenset(failed),
            completed_candidate_keys=frozenset(completed_candidates),
            candidates=tuple(candidates),
            failures=tuple(failures),
            gate_decisions=tuple(gates),
        )

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload_json FROM v4_records WHERE record_id = ?", (record_id,)
        ).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None

    def list_runs(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT DISTINCT run_id FROM v4_records ORDER BY run_id"
        ).fetchall()
        return [str(row[0]) for row in rows]

    # ───────────────────────── 内部 ─────────────────────────

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
        applied = {
            int(row[0])
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        if 1 not in applied:
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE v4_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_type TEXT NOT NULL,
                    record_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    plan_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                ) STRICT;
                CREATE INDEX idx_v4_records_run_id ON v4_records (run_id);
                CREATE INDEX idx_v4_records_plan_id ON v4_records (plan_id);
                INSERT INTO schema_migrations (version, applied_at) VALUES (1, '2026-08-04T00:00:00Z');
                COMMIT;
                """
            )


class MemorySink:
    """仅用于测试/并行工作线程的内存 sink（文档 §7.5：mock sink 产物不得进入 release 路径）。

    记录保留幂等键，供主线程合并到真实 AppendSink（并行生成时线程内收集）。
    """

    def __init__(self) -> None:
        self._records: list[V4Record] = []
        self._ordered: list[tuple[str, V4Record]] = []
        self._by_key: dict[str, V4Record] = {}

    def append(self, record: V4Record, idempotency_key: str) -> DurableAppendResult:
        existing = self._by_key.get(idempotency_key)
        if existing is not None:
            if existing.to_dict() != record.to_dict():
                raise IdempotencyConflictError(
                    f"幂等键 {idempotency_key} 已存在且内容不同"
                )
            return DurableAppendResult(record.header.record_id, False)
        self._by_key[idempotency_key] = record
        self._records.append(record)
        self._ordered.append((idempotency_key, record))
        return DurableAppendResult(record.header.record_id, True)

    def items(self) -> list[tuple[str, V4Record]]:
        """按追加顺序返回 (幂等键, 记录) 对，供主线程合并。"""
        return list(self._ordered)

    def read_progress(self, run_id: str) -> RunProgress:
        candidates: list[CandidateRecordV4] = []
        failures: list[FailureRecord] = []
        gates: list[GateDecisionRecord] = []
        run_started = generation_completed = False
        run_started_plan_id: str | None = None
        run_started_lock_hash: str | None = None
        completed: set[str] = set()
        failed: set[str] = set()
        completed_candidates: set[tuple[str, int]] = set()
        for record in self._records:
            if record.header.run_id != run_id:
                continue
            if isinstance(record, RunEvent):
                if record.event_name == "run_started":
                    run_started = True
                    run_started_plan_id = record.header.plan_id
                    run_started_lock_hash = record.header.package_lock_hash
                elif record.event_name == "generation_completed":
                    generation_completed = True
            elif isinstance(record, CandidateRecordV4):
                candidates.append(record)
                if record.header.plan_id:
                    completed.add(record.header.plan_id)
                    completed_candidates.add(
                        (record.header.plan_id, int(record.candidate_no))
                    )
            elif isinstance(record, FailureRecord):
                failures.append(record)
                if record.header.plan_id:
                    failed.add(record.header.plan_id)
            elif isinstance(record, GateDecisionRecord):
                gates.append(record)
        return RunProgress(
            run_id=run_id,
            run_started=run_started,
            generation_completed=generation_completed,
            run_started_plan_id=run_started_plan_id,
            run_started_lock_hash=run_started_lock_hash,
            completed_plan_ids=frozenset(completed),
            failed_plan_ids=frozenset(failed),
            completed_candidate_keys=frozenset(completed_candidates),
            candidates=tuple(candidates),
            failures=tuple(failures),
            gate_decisions=tuple(gates),
        )

    def close(self) -> None:
        pass
