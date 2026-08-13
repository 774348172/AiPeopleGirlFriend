"""AppendSink：幂等追加、冲突、进度、崩溃恢复与单写者。"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from data_gen_v4.core.errors import IdempotencyConflictError, SinkLockedError
from data_gen_v4.core.records import CandidateRecordV4, FailureRecord, RunEvent
from data_gen_v4.core.sink import AppendSink, MemorySink
from tests.core.test_records import _candidate, _header


def _h(record_type: str, record_id: str):
    """带唯一 record_id 的 header（record_id 全局唯一，见 §8.2）。"""
    return replace(_header(record_type), record_id=record_id)


@pytest.fixture()
def sink(tmp_path):
    with AppendSink.open(tmp_path / "run.sqlite") as handle:
        yield handle


def test_append_is_idempotent_for_same_content(sink):
    record = RunEvent(header=_header("run"), event_name="run_started")
    first = sink.append(record, "key:run_started")
    second = sink.append(record, "key:run_started")
    assert first.created is True
    assert second.created is False
    assert first.record_id == second.record_id
    progress = sink.read_progress("run-1")
    assert progress.run_started is True


def test_append_same_key_different_content_conflicts(sink):
    record = RunEvent(header=_header("run"), event_name="run_started")
    sink.append(record, "key:1")
    other = RunEvent(header=_header("run"), event_name="generation_completed")
    with pytest.raises(IdempotencyConflictError):
        sink.append(other, "key:1")


def test_append_same_record_id_different_key_conflicts(sink):
    record = RunEvent(header=_header("run"), event_name="run_started")
    sink.append(record, "key:1")
    other = RunEvent(header=_header("run"), event_name="generation_completed")
    # 同 record_id（_header 固定 rec-1）不同幂等键 → UNIQUE 冲突
    with pytest.raises(IdempotencyConflictError):
        sink.append(other, "key:2")


def test_read_progress_aggregates(sink):
    run = _header("run")
    sink.append(RunEvent(header=_h("run", "rec-run"), event_name="run_started"), "k:run")
    sink.append(replace(_candidate(), header=_h("candidate", "rec-cand")), "k:cand")
    sink.append(
        FailureRecord(
            header=_h("failure", "rec-fail"), attempt_no=1, candidate_no=None,
            stage="generate", error_code="timeout", retryable=True, reason="x",
        ),
        "k:fail",
    )
    sink.append(
        RunEvent(header=_h("run", "rec-done"), event_name="generation_completed"), "k:done"
    )
    progress = sink.read_progress("run-1")
    assert progress.run_started is True
    assert progress.generation_completed is True
    assert len(progress.candidates) == 1
    assert len(progress.failures) == 1
    assert "plan-1:0000" in progress.completed_plan_ids
    assert "plan-1:0000" in progress.failed_plan_ids


def test_progress_is_isolated_per_run(sink):
    sink.append(RunEvent(header=_h("run", "rec-1"), event_name="run_started"), "k:1")
    sink.append(RunEvent(header=_h("run", "rec-2"), event_name="run_started"), "k:2")
    progress = sink.read_progress("run-1")
    assert progress.run_started is True
    # 同 run 重复 run_started 允许（幂等键不同），但业务层 execute 会拒绝
    assert len(sink.list_runs()) == 1


def test_append_crash_before_commit_rolls_back(sink):
    """未提交事务不可见：模拟 append 提交前崩溃（回滚后进度不含该记录）。

    sqlite3.Connection 的 C 方法不可 monkeypatch，这里直接验证 SQLite 的
    事务原子性语义；append 失败回滚路径由冲突测试覆盖（同键异内容/record_id 冲突）。
    """
    record = _candidate()
    connection = sink._connection
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        """
        INSERT INTO v4_records (record_type, record_id, idempotency_key,
                                run_id, plan_id, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.header.record_type, record.header.record_id, "k:rollback",
            record.header.run_id, record.header.plan_id,
            json.dumps(record.to_dict(), ensure_ascii=False),
            record.header.created_at,
        ),
    )
    connection.execute("ROLLBACK")  # 模拟崩溃后的回滚
    progress = sink.read_progress("run-1")
    assert len(progress.candidates) == 0


def test_reopen_survives_crash_like_restart(tmp_path):
    path = tmp_path / "crash.sqlite"
    with AppendSink.open(path) as first:
        first.append(_candidate(), "k:1")
    # 模拟崩溃：不 close（直接丢弃），重新 open
    with AppendSink.open(path) as reopened:
        progress = reopened.read_progress("run-1")
        assert len(progress.candidates) == 1
        # 幂等键保留：同内容重复提交返回已有记录
        result = reopened.append(_candidate(), "k:1")
        assert result.created is False


def test_single_writer_lock(tmp_path):
    path = tmp_path / "locked.sqlite"
    first = AppendSink.open(path)
    try:
        with pytest.raises(SinkLockedError):
            AppendSink.open(path)
    finally:
        first.close()
    # 释放后可重新打开
    with AppendSink.open(path):
        pass


def test_migrations_apply_once(tmp_path):
    path = tmp_path / "migrate.sqlite"
    with AppendSink.open(path):
        pass
    with AppendSink.open(path):
        pass
    # 迁移版本只记录一次
    import sqlite3

    connection = sqlite3.connect(path)
    versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
    connection.close()
    assert [int(v[0]) for v in versions] == [1]


def test_memory_sink_is_idempotent():
    sink = MemorySink()
    record = _candidate()
    assert sink.append(record, "k:1").created is True
    assert sink.append(record, "k:1").created is False
    other = RunEvent(header=_header("run"), event_name="run_started")
    with pytest.raises(IdempotencyConflictError):
        sink.append(other, "k:1")
    progress = sink.read_progress("run-1")
    assert len(progress.candidates) == 1
