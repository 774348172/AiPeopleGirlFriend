"""ExportAdapter：TrainingRecord 语义层 → 训练/审计格式（《数据生成器v4设计》§15.3）。

ExportAdapter 只做格式序列化与往返验证，不构造监督信息或 loss mask（ModeAdapter 职责）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from data_gen_v4.adapters.modes.training import TrainingRecordV4

SerializedRecord = str


@dataclass(frozen=True, slots=True)
class ExportResult:
    record: SerializedRecord
    validated: bool = True


class ShareGPTReplyExportAdapter:
    """REPLY TrainingRecord → sharegpt JSONL 行。

    保留 system anchor（若有）与 mode 标识；supervised 信息由 assistant 位置隐含。
    """

    def render(self, training_record: TrainingRecordV4, export_profile: Any = None) -> str:
        conversations: list[dict[str, str]] = []
        if training_record.system_anchor:
            conversations.append({"from": "system", "value": training_record.system_anchor})
        for message in training_record.messages:
            side = "gpt" if message["role"] == "assistant" else "human"
            conversations.append({"from": side, "value": message["content"]})
        return _dumps({"conversations": conversations})

    def validate_roundtrip(self, serialized: str) -> list[dict[str, Any]]:
        doc = _loads(serialized)
        conversations = doc.get("conversations")
        if not isinstance(conversations, list) or not conversations:
            return [{"ok": False, "reason_code": "missing_conversations"}]
        errors = []
        if conversations[0]["from"] == "system" and not conversations[0]["value"]:
            errors.append({"ok": False, "reason_code": "empty_system_anchor"})
        for conversation in conversations:
            if conversation["from"] not in ("system", "human", "gpt"):
                errors.append(
                    {"ok": False, "reason_code": f"bad_role:{conversation['from']}"}
                )
            if not str(conversation.get("value", "")).strip():
                errors.append({"ok": False, "reason_code": "empty_value"})
        return errors or [{"ok": True}]

    def roundtrip_to_training_record(
        self, serialized: str, original: TrainingRecordV4
    ) -> TrainingRecordV4:
        """往返重建：断言消息顺序与监督索引在序列化后无丢失。"""
        doc = _loads(serialized)
        messages = [
            {"role": "assistant" if c["from"] == "gpt" else "human", "content": c["value"]}
            for c in doc["conversations"]
            if c["from"] != "system"
        ]
        supervised = [i for i, m in enumerate(messages) if m["role"] == "assistant"]
        return TrainingRecordV4(
            sample_id=original.sample_id,
            mode=original.mode,
            render_profile_id=original.render_profile_id,
            messages=messages,
            supervised_message_indexes=supervised,
            supervised_token_count=original.supervised_token_count,
            protocol_snapshot_id=original.protocol_snapshot_id,
            system_anchor=original.system_anchor,
            split=original.split,
        )


class StructuredProtocolSFTExportAdapter:
    """协议 TrainingRecord → 带 mode discriminator 的 SFT JSONL 行。

    system 条携带协议标识与 grammar 约束；只监督 gpt（JSON target）条。
    """

    MODE_DISCRIMINATOR = {
        "RECALL_PLAN": "mode=RECALL_PLAN; 输出符合 recall_plan_target 冻结 schema 的 JSON",
        "MEMORY_PROPOSE": "mode=MEMORY_PROPOSE; 输出符合 memory_propose_target 冻结 schema 的 JSON",
    }

    def render(self, training_record: TrainingRecordV4, export_profile: Any = None) -> str:
        discriminator = self.MODE_DISCRIMINATOR.get(
            training_record.mode, f"mode={training_record.mode}"
        )
        system = training_record.system_anchor or discriminator
        conversations = [
            {"from": "system", "value": system},
            {"from": "human", "value": training_record.messages[0]["content"]},
            {"from": "gpt", "value": training_record.messages[1]["content"]},
        ]
        return _dumps({"conversations": conversations})

    def validate_roundtrip(self, serialized: str) -> list[dict[str, Any]]:
        doc = _loads(serialized)
        conversations = doc.get("conversations")
        if not isinstance(conversations, list) or len(conversations) != 3:
            return [{"ok": False, "reason_code": "protocol_expect_3_messages"}]
        roles = [c["from"] for c in conversations]
        if roles != ["system", "human", "gpt"]:
            return [{"ok": False, "reason_code": f"bad_roles:{roles}"}]
        return [{"ok": True}]

    def roundtrip_to_training_record(
        self, serialized: str, original: TrainingRecordV4
    ) -> TrainingRecordV4:
        doc = _loads(serialized)
        messages = [
            {"role": "human", "content": doc["conversations"][1]["value"]},
            {"role": "assistant", "content": doc["conversations"][2]["value"]},
        ]
        return TrainingRecordV4(
            sample_id=original.sample_id,
            mode=original.mode,
            render_profile_id=original.render_profile_id,
            messages=messages,
            supervised_message_indexes=[1],
            supervised_token_count=original.supervised_token_count,
            protocol_snapshot_id=original.protocol_snapshot_id,
            system_anchor=doc["conversations"][0]["value"],
            split=original.split,
        )


class RawAuditJsonlExportAdapter:
    """原始审计导出：record dict 原样序列化为 JSONL 行（§15.3 RawAuditJsonl）。"""

    def render(self, record: dict[str, Any], export_profile: Any = None) -> str:
        return _dumps(record)

    def validate_roundtrip(self, serialized: str) -> list[dict[str, Any]]:
        doc = _loads(serialized)
        if not isinstance(doc, dict) or "record_id" not in doc:
            return [{"ok": False, "reason_code": "missing_record_id"}]
        return [{"ok": True}]

    def roundtrip_to_record(self, serialized: str, original: dict[str, Any]) -> dict[str, Any]:
        return _loads(serialized)


def _dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(text: str) -> Any:
    import json

    return json.loads(text)
