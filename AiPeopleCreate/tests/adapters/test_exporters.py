"""TrainingRecordV4 语义层与 ExportAdapter 序列化/往返。"""
from __future__ import annotations

import json

import pytest

from data_gen_v4.adapters.exporters.export import (
    RawAuditJsonlExportAdapter,
    ShareGPTReplyExportAdapter,
    StructuredProtocolSFTExportAdapter,
)
from data_gen_v4.adapters.modes.training import (
    TrainingRecordV4,
    estimate_token_count,
)


def _reply_record() -> TrainingRecordV4:
    return TrainingRecordV4(
        sample_id="s1",
        mode="REPLY",
        render_profile_id="reply-runtime-v1",
        messages=[
            {"role": "human", "content": "你回来啦。"},
            {"role": "assistant", "content": "嗯，加班到现在。"},
            {"role": "human", "content": "吃饭了吗？"},
            {"role": "assistant", "content": "点了外卖。"},
        ],
        supervised_message_indexes=[1, 3],
        supervised_token_count=14,
        protocol_snapshot_id="reply-protocol-v1",
        system_anchor="身份锚：第一人称口语角色",
    )


def _protocol_record() -> TrainingRecordV4:
    return TrainingRecordV4(
        sample_id="s2",
        mode="RECALL_PLAN",
        render_profile_id="recall-plan-v1",
        messages=[
            {"role": "human", "content": '{"visible_cues": []}'},
            {"role": "assistant", "content": '{"action": "NO_OP", "reason": "r"}'},
        ],
        supervised_message_indexes=[1],
        supervised_token_count=12,
        protocol_snapshot_id="protocol-v1",
        system_anchor=None,
    )


def test_training_record_roundtrip():
    record = _reply_record()
    assert TrainingRecordV4.from_dict(record.to_dict()) == record


def test_token_estimate_counts_supervised_content():
    record = _reply_record()
    # 监督两条 assistant："嗯，加班到现在。"(8) + "点了外卖。"(5) = 13
    assert estimate_token_count(record.messages, [1, 3]) == 13


def test_sharegpt_render_keeps_system_anchor_and_order():
    adapter = ShareGPTReplyExportAdapter()
    line = adapter.render(_reply_record())
    doc = json.loads(line)
    assert doc["conversations"][0] == {"from": "system", "value": "身份锚：第一人称口语角色"}
    roles = [c["from"] for c in doc["conversations"]]
    assert roles == ["system", "human", "gpt", "human", "gpt"]


def test_sharegpt_roundtrip_is_lossless():
    adapter = ShareGPTReplyExportAdapter()
    record = _reply_record()
    line = adapter.render(record)
    assert adapter.validate_roundtrip(line) == [{"ok": True}]
    restored = adapter.roundtrip_to_training_record(line, record)
    assert restored.messages == record.messages
    assert restored.supervised_message_indexes == record.supervised_message_indexes
    assert restored.mode == record.mode


def test_sharegpt_rejects_bad_role():
    adapter = ShareGPTReplyExportAdapter()
    line = json.dumps({"conversations": [{"from": "robot", "value": "x"}]})
    results = adapter.validate_roundtrip(line)
    assert any(not r["ok"] for r in results)


def test_structured_sft_has_three_messages():
    adapter = StructuredProtocolSFTExportAdapter()
    line = adapter.render(_protocol_record())
    doc = json.loads(line)
    assert [c["from"] for c in doc["conversations"]] == ["system", "human", "gpt"]
    assert "RECALL_PLAN" in doc["conversations"][0]["value"]
    # 只监督 gpt 条（roundtrip 重建监督索引 [1]）
    restored = adapter.roundtrip_to_training_record(line, _protocol_record())
    assert restored.supervised_message_indexes == [1]
    assert restored.mode == "RECALL_PLAN"


def test_structured_sft_rejects_wrong_shape():
    adapter = StructuredProtocolSFTExportAdapter()
    line = json.dumps({"conversations": [{"from": "system", "value": "s"}]})
    assert any(not r["ok"] for r in adapter.validate_roundtrip(line))


def test_raw_audit_roundtrip():
    adapter = RawAuditJsonlExportAdapter()
    record = {"record_id": "rec-1", "record_type": "candidate", "payload": {"a": 1}}
    line = adapter.render(record)
    assert adapter.validate_roundtrip(line) == [{"ok": True}]
    assert adapter.roundtrip_to_record(line, record) == record


def test_raw_audit_rejects_missing_record_id():
    adapter = RawAuditJsonlExportAdapter()
    line = json.dumps({"record_type": "run"})
    assert any(not r["ok"] for r in adapter.validate_roundtrip(line))
