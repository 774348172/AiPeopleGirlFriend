"""阶段 4 D-4（原子发布）验收。

- manifest 事务：写盘成功 → release_created 事件落盘；
- **G4：release 无 manifest 不产出**——事件失败 → manifest 回滚、无 release；
- manifest 结构对齐契约（files/freeze.manifest_sha256/sample_ids/分布）；
- manifest_sha256 自洽。
"""
from __future__ import annotations

import json

import pytest

from data_gen_v4.core.records import LineageHeaderV4
from data_gen_v4.core.release import ReleaseInput, build_manifest, publish_release
from data_gen_v4.core.sink import AppendSink, MemorySink


def _input(tmp_path) -> ReleaseInput:
    data_file = tmp_path / "train.jsonl"
    data_file.write_text(
        '{"conversations": [{"from": "gpt", "content": "你好呀"}]}\n'
        '{"conversations": [{"from": "gpt", "content": "今天吃什么"}]}\n',
        encoding="utf-8",
    )
    return ReleaseInput(
        dataset_id="test-ds-1",
        character_id="qinweixi",
        dataset_family="visible_reply",
        split_policy={"train": 0.8, "dev": 0.1, "test": 0.1},
        samples=[
            {
                "sample_id": "s1",
                "split_anchor_ids": ["family:f1"],
                "family_id": "f1",
                "task_type": "reply_casual",
                "evidence_state": "supported",
                "desired_policy": "answer",
                "text": "你好呀",
                "_split": "train",
            },
            {
                "sample_id": "s2",
                "split_anchor_ids": ["family:f2"],
                "family_id": "f2",
                "task_type": "reply_casual",
                "evidence_state": "supported",
                "desired_policy": "answer",
                "text": "今天吃什么",
                "_split": "dev",
            },
        ],
        files_by_split={"train": [data_file]},
        package_lock_hash="sha256:" + "a" * 64,
        profile_id="qinweixi",
        mode="REPLY",
        gate_summary={"accepted": 2},
    )


def _header() -> LineageHeaderV4:
    return LineageHeaderV4(
        record_type="run",
        run_id="publish-test",
        plan_id="test-ds-1",
        package_lock_hash="sha256:" + "a" * 64,
        profile_id="qinweixi",
        profile_snapshot_id="",
        protocol_bundle_id="relationship-runtime-v1",
        recipe_id="",
        mode="REPLY",
        task_type="",
        family_id="",
        generator_version="0.1.0",
    )


def test_publish_release_writes_manifest_and_event(tmp_path):
    manifest = build_manifest(_input(tmp_path))
    out = tmp_path / "manifest.json"
    sink = MemorySink()
    publish_release(manifest, out, sink, run_id="publish", header=_header())
    # manifest 落盘 + release_created 事件
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["dataset_id"] == "test-ds-1"
    assert saved["freeze"]["immutable"] is True
    assert saved["freeze"]["manifest_sha256"].startswith("sha256:")
    events = [r for r in sink.items() if r[1].event_name == "release_created"]
    assert len(events) == 1


def test_manifest_sha256_self_consistent(tmp_path):
    manifest = build_manifest(_input(tmp_path))
    out = tmp_path / "m.json"
    publish_release(manifest, out, MemorySink(), run_id="p", header=_header())
    saved = json.loads(out.read_text(encoding="utf-8"))
    # manifest_sha256 = sha256(除 freeze 外的规范 JSON)
    import hashlib

    body = {k: v for k, v in saved.items() if k != "freeze"}
    expected = "sha256:" + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert saved["freeze"]["manifest_sha256"] == expected


def test_release_without_manifest_not_produced(tmp_path):
    """G4：manifest 写入失败（模拟磁盘错误）→ 无 release 事件、无 manifest 残留。"""
    manifest = build_manifest(_input(tmp_path))
    # 用不可写目录模拟失败（权限/路径错误）
    out = tmp_path / "no_such_dir" / "manifest.json"
    sink = MemorySink()
    with pytest.raises(OSError):
        publish_release(manifest, out, sink, run_id="publish", header=_header())
    assert not out.exists()
    events = [r for r in sink.items() if r[1].event_name == "release_created"]
    assert events == []


def test_event_failure_rolls_back_manifest(tmp_path):
    """事件写失败 → manifest 回滚删除（无 manifest 不产出 release 的反向）。"""
    manifest = build_manifest(_input(tmp_path))
    out = tmp_path / "m2.json"

    class _FailingSink:
        def append(self, record, key):
            raise RuntimeError("sink down")

    with pytest.raises(RuntimeError):
        publish_release(manifest, out, _FailingSink(), run_id="publish", header=_header())
    assert not out.exists(), "事件失败必须回滚 manifest"


def test_manifest_structure_contract_fields(tmp_path):
    manifest = build_manifest(_input(tmp_path))
    assert manifest["schema_version"] == "aip.dataset_manifest.v1"
    assert manifest["status"] == "frozen"
    assert manifest["files"][0]["split"] == "train"
    assert manifest["files"][0]["records"] == 2
    assert manifest["files"][0]["sha256"]
    assert set(manifest["sample_ids"]) <= {"train", "dev", "test"}
    assert manifest["distributions"]["task_type"]["reply_casual"] == 2
    assert manifest["gate_summary"]["accepted"] == 2
