from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

import pytest

from runtime._ledger import (
    DatabaseInUseError,
    EventLedger,
    LedgerError,
    MigrationError,
    RequestConflictError,
)
from runtime.contracts import UserMessage


NOW = datetime(2026, 8, 4, 4, 0, tzinfo=timezone.utc)


def message(request_id: str = "r1", text: str = "周末一起看电影") -> UserMessage:
    return UserMessage(request_id, "c1", text, NOW, "Asia/Shanghai")


def test_migrations_are_applied_once_and_survive_restart(tmp_path):
    path = tmp_path / "relationship.sqlite3"
    ledger = EventLedger.open(path)
    first = ledger.append_user_message(message()).event
    ledger.close()

    reopened = EventLedger.open(path)
    try:
        assert reopened.list_events() == [first]
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT version FROM schema_migrations").fetchall() == [
                (1,),
                (2,),
                (3,),
                (4,),
                (5,),
                (6,),
                (7,),
                (8,),
            ]
    finally:
        reopened.close()


def test_second_ledger_cannot_open_the_same_player_database(tmp_path):
    path = tmp_path / "relationship.sqlite3"
    first = EventLedger.open(path)
    try:
        with pytest.raises(DatabaseInUseError, match="already in use"):
            EventLedger.open(path)
    finally:
        first.close()

    reopened = EventLedger.open(path)
    reopened.close()


def test_sequence_numbers_are_monotonic_per_conversation(tmp_path):
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        user = ledger.append_user_message(message()).event
        reply = ledger.append_character_message(
            request_id="r1",
            conversation_id="c1",
            user_event_id=user.event_id,
            text="好啊。",
        )
        assert [event.sequence_no for event in ledger.list_events("c1")] == [1, 2]
        assert reply.causation_event_id == user.event_id
    finally:
        ledger.close()


def test_repeated_user_request_is_idempotent(tmp_path):
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        first = ledger.append_user_message(message())
        second = ledger.append_user_message(message())
        assert first.created is True
        assert second.created is False
        assert second.event.event_id == first.event.event_id
        assert len(ledger.list_events()) == 1
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "changed",
    [
        UserMessage("r1", "other", "周末一起看电影", NOW, "Asia/Shanghai"),
        UserMessage("r1", "c1", "改成打游戏", NOW, "Asia/Shanghai"),
        UserMessage("r1", "c1", "周末一起看电影", NOW, "UTC"),
        UserMessage("r1", "c1", "周末一起看电影", NOW, "Asia/Shanghai", "stt"),
        UserMessage(
            "r1",
            "c1",
            "周末一起看电影",
            datetime(2026, 8, 4, 4, 1, tzinfo=timezone.utc),
            "Asia/Shanghai",
        ),
    ],
)
def test_reused_request_id_with_changed_input_conflicts(tmp_path, changed):
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        ledger.append_user_message(message())
        with pytest.raises(RequestConflictError):
            ledger.append_user_message(changed)
    finally:
        ledger.close()


def test_events_are_append_only_at_the_database_level(tmp_path):
    path = tmp_path / "relationship.sqlite3"
    ledger = EventLedger.open(path)
    event = ledger.append_user_message(message()).event
    ledger.close()

    connection = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE events SET payload_json = ? WHERE event_id = ?",
                (json.dumps({"text": "changed"}), event.event_id),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM events WHERE event_id = ?", (event.event_id,))
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [("actor", "intruder"), ("event_type", "unknown"), ("source", "cloud")],
)
def test_invalid_enums_are_rejected_by_sqlite(tmp_path, column, value):
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        values = {
            "actor": "user",
            "event_type": "message",
            "source": "typed",
        }
        values[column] = value
        with pytest.raises(sqlite3.IntegrityError):
            ledger._connection.execute(  # internal seam constraint test
                """
                INSERT INTO events (
                    event_id, request_id, conversation_id, sequence_no,
                    occurred_at, recorded_at, occurred_timezone, actor,
                    event_type, payload_json, source, schema_version
                ) VALUES (?, ?, 'c1', 1, ?, ?, 'UTC', ?, ?, ?, ?, 1)
                """,
                (
                    "invalid-" + column,
                    "invalid-" + column,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    values["actor"],
                    values["event_type"],
                    json.dumps({"text": "test"}),
                    values["source"],
                ),
            )
    finally:
        ledger.close()


def test_invalid_json_and_missing_causation_are_rejected(tmp_path):
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            ledger._connection.execute(
                """
                INSERT INTO events (
                    event_id, request_id, conversation_id, sequence_no,
                    occurred_at, recorded_at, occurred_timezone, actor,
                    event_type, payload_json, source, schema_version
                ) VALUES ('e-bad', 'r-bad', 'c1', 1, ?, ?, 'UTC',
                          'user', 'message', 'not-json', 'typed', 1)
                """,
                (NOW.isoformat(), NOW.isoformat()),
            )
        with pytest.raises(LedgerError, match="does not exist"):
            ledger.append_character_message(
                request_id="r1",
                conversation_id="c1",
                user_event_id="missing",
                text="回复",
            )
    finally:
        ledger.close()


def test_fts_is_maintained_by_the_event_insert_trigger(tmp_path):
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    try:
        user = ledger.append_user_message(message()).event
        assert ledger.search_messages("一起看") == [user.event_id]
    finally:
        ledger.close()


def test_unknown_applied_migration_stops_startup(tmp_path):
    path = tmp_path / "relationship.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT"
    )
    connection.execute("INSERT INTO schema_migrations VALUES (999, ?)", (NOW.isoformat(),))
    connection.commit()
    connection.close()

    with pytest.raises(MigrationError, match="unknown migrations"):
        EventLedger.open(path)


def test_user_append_p95_is_below_fifty_milliseconds(tmp_path):
    ledger = EventLedger.open(tmp_path / "relationship.sqlite3")
    durations = []
    try:
        for index in range(50):
            started = time.perf_counter()
            ledger.append_user_message(message(f"r-{index}", f"第{index}条测试消息"))
            durations.append((time.perf_counter() - started) * 1000)
    finally:
        ledger.close()
    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 50, f"SQLite append p95 was {p95:.2f} ms"
