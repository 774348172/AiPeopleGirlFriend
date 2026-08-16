from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ._context import ContextAssembler
from ._ledger import EventLedger
from ._settings import RuntimeConfig
from .contracts import UserMessage


SCALE_CONVERSATION = "stage5-scale"
OLD_EPOCH = "stage5-scale-old"
CURRENT_EPOCH = "stage5-scale-current"
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build_scale_database(database_path: Path, event_count: int) -> float:
    if event_count <= 0 or event_count % 2:
        raise ValueError("event_count must be a positive even number")
    path = Path(database_path)
    if path.exists():
        raise FileExistsError(f"scale database already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = EventLedger.open(path, clock=lambda: BASE_TIME)
    ledger.close()

    started = time.perf_counter()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            INSERT INTO conversation_epochs (
                epoch_id, conversation_id, ordinal, state,
                opened_sequence_no, closed_sequence_no, close_reason,
                budget_version, created_at, closed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    OLD_EPOCH,
                    SCALE_CONVERSATION,
                    1,
                    "closed",
                    1,
                    event_count,
                    "context_budget",
                    1,
                    _iso(BASE_TIME),
                    _iso(BASE_TIME + timedelta(seconds=event_count)),
                ),
                (
                    CURRENT_EPOCH,
                    SCALE_CONVERSATION,
                    2,
                    "open",
                    event_count + 1,
                    None,
                    None,
                    1,
                    _iso(BASE_TIME + timedelta(seconds=event_count)),
                    None,
                ),
            ),
        )
        batch_size = 2000
        for batch_start in range(0, event_count, batch_size):
            batch_end = min(event_count, batch_start + batch_size)
            events: list[tuple[object, ...]] = []
            mappings: list[tuple[object, ...]] = []
            for index in range(batch_start, batch_end):
                sequence_no = index + 1
                turn_no = index // 2
                is_user = index % 2 == 0
                event_id = f"scale-event-{sequence_no:06d}"
                request_id = f"scale-request-{turn_no:06d}"
                actor = "user" if is_user else "character"
                marker = " 银杏航站" if index % 10000 == 0 else ""
                text = f"合成对话第{sequence_no}条{marker}"
                events.append(
                    (
                        event_id,
                        request_id,
                        SCALE_CONVERSATION,
                        sequence_no,
                        _iso(BASE_TIME + timedelta(seconds=index)),
                        _iso(BASE_TIME + timedelta(seconds=index)),
                        "UTC",
                        actor,
                        "message",
                        json.dumps(
                            {"text": text, "status": "complete"},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "typed" if is_user else "runtime",
                        None if is_user else f"scale-event-{sequence_no - 1:06d}",
                        None,
                        1,
                    )
                )
                mappings.append((event_id, OLD_EPOCH, sequence_no, 0))
            connection.executemany(
                """
                INSERT INTO events (
                    event_id, request_id, conversation_id, sequence_no,
                    occurred_at, recorded_at, occurred_timezone,
                    actor, event_type, payload_json, source,
                    causation_event_id, supersedes_event_id, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                events,
            )
            connection.executemany(
                """
                INSERT INTO event_epochs (
                    event_id, epoch_id, ordinal_in_epoch, estimated_tokens
                ) VALUES (?, ?, ?, ?)
                """,
                mappings,
            )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return (time.perf_counter() - started) * 1000


def benchmark_scale_database(
    database_path: Path, *, event_count: int, samples: int = 50
) -> dict[str, Any]:
    if samples <= 0:
        raise ValueError("samples must be positive")
    path = Path(database_path)
    ledger = EventLedger.open(path, clock=lambda: BASE_TIME + timedelta(days=2))
    try:
        phrase_samples = _sample(
            samples,
            lambda: ledger.find_recall_episodes(
                conversation_id=SCALE_CONVERSATION,
                excluded_epoch_id=CURRENT_EPOCH,
                phrases=("银杏航站",),
                time_ranges=(),
                candidate_limit=20,
            ),
        )
        time_end = BASE_TIME + timedelta(seconds=event_count)
        time_start = time_end - timedelta(seconds=min(3600, event_count))
        time_samples = _sample(
            samples,
            lambda: ledger.find_recall_episodes(
                conversation_id=SCALE_CONVERSATION,
                excluded_epoch_id=CURRENT_EPOCH,
                phrases=(),
                time_ranges=((time_start, time_end),),
                candidate_limit=20,
            ),
        )

        query = ledger.append_user_message(
            UserMessage(
                "scale-query",
                SCALE_CONVERSATION,
                "你还记得银杏航站吗？",
                BASE_TIME + timedelta(days=2),
                "Asia/Shanghai",
            )
        )
        query_snapshot = ledger.load_epoch_snapshot(query.event.event_id)
        config = RuntimeConfig(path.parent, database_name=path.name)
        context_assembler = ContextAssembler(
            ledger=ledger,
            context_size=config.context_size,
            reply_reserve_tokens=config.reply_reserve_tokens,
            safety_margin_tokens=config.context_safety_margin_tokens,
            recall_candidate_limit=config.recall_candidate_limit,
            recall_evidence_limit=config.recall_evidence_limit,
            recall_excerpt_chars=config.recall_excerpt_chars,
            recall_target_tokens=config.recall_target_tokens,
        )
        recall_context_samples = _sample(
            samples,
            lambda: context_assembler.assemble(
                query_snapshot,
                current_user_event_id=query.event.event_id,
                current_user_text=query.event.text,
                current_time=BASE_TIME + timedelta(days=2),
                current_timezone="Asia/Shanghai",
            ),
        )

        normal = ledger.append_user_message(
            UserMessage(
                "scale-normal",
                SCALE_CONVERSATION,
                "普通问题",
                BASE_TIME + timedelta(days=2, seconds=1),
                "Asia/Shanghai",
            )
        )
        normal_snapshot = ledger.load_epoch_snapshot(normal.event.event_id)
        normal_context_samples = _sample(
            samples,
            lambda: context_assembler.assemble(
                normal_snapshot,
                current_user_event_id=normal.event.event_id,
                current_user_text=normal.event.text,
                current_time=BASE_TIME + timedelta(days=2, seconds=1),
                current_timezone="Asia/Shanghai",
            ),
        )
        phrase_plan = _phrase_query_plan(ledger._connection)
        time_plan = _time_query_plan(ledger._connection, time_start, time_end)
    finally:
        ledger.close()

    return {
        "event_count": event_count,
        "samples": samples,
        "database_bytes": path.stat().st_size,
        "phrase_query": _distribution(phrase_samples),
        "time_query": _distribution(time_samples),
        "normal_context": _distribution(normal_context_samples),
        "recall_context": _distribution(recall_context_samples),
        "phrase_query_plan": phrase_plan,
        "time_query_plan": time_plan,
        "fts_virtual_index": any(
            "VIRTUAL TABLE INDEX" in item for item in phrase_plan
        ),
        "conversation_time_index": any(
            "ix_events_conversation_time" in item for item in time_plan
        ),
    }


def _sample(count: int, operation) -> list[float]:
    operation()
    values: list[float] = []
    for _ in range(count):
        started = time.perf_counter()
        operation()
        values.append((time.perf_counter() - started) * 1000)
    return values


def _distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "max_ms": round(max(ordered), 3),
    }


def _phrase_query_plan(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT e.event_id
        FROM event_fts
        JOIN events AS e ON e.event_id = event_fts.event_id
        JOIN event_epochs AS ee ON ee.event_id = e.event_id
        WHERE event_fts MATCH '"silver"'
          AND e.conversation_id = ? AND ee.epoch_id <> ?
        ORDER BY bm25(event_fts), e.sequence_no, e.event_id LIMIT 20
        """,
        (SCALE_CONVERSATION, CURRENT_EPOCH),
    ).fetchall()
    return [str(row[3]) for row in rows]


def _time_query_plan(
    connection: sqlite3.Connection, start: datetime, end: datetime
) -> list[str]:
    midpoint = start + (end - start) / 2
    rows = connection.execute(
        """
        EXPLAIN QUERY PLAN
        SELECT e.event_id
        FROM events AS e
        JOIN event_epochs AS ee ON ee.event_id = e.event_id
        WHERE e.conversation_id = ? AND ee.epoch_id <> ?
          AND e.event_type = 'message' AND e.actor IN ('user', 'character')
          AND e.occurred_at >= ? AND e.occurred_at < ?
        ORDER BY ABS(julianday(e.occurred_at) - julianday(?)),
                 e.sequence_no, e.event_id LIMIT 20
        """,
        (
            SCALE_CONVERSATION,
            CURRENT_EPOCH,
            _iso(start),
            _iso(end),
            _iso(midpoint),
        ),
    ).fetchall()
    return [str(row[3]) for row in rows]


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Stage 5 continuity")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--events", type=int, default=100000)
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="reuse an existing benchmark database instead of rebuilding it",
    )
    args = parser.parse_args()

    if args.reuse:
        if not args.database.exists():
            raise FileNotFoundError(f"scale database does not exist: {args.database}")
        build_ms = None
    else:
        build_ms = build_scale_database(args.database, args.events)
    result = benchmark_scale_database(
        args.database, event_count=args.events, samples=args.samples
    )
    result["build_ms"] = round(build_ms, 3) if build_ms is not None else None
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
