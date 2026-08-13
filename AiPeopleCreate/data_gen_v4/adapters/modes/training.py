"""TrainingRecordV4 语义层（《数据生成器v4设计》§7.3、§15.3）。

ModeAdapter.render_training 产出的语义层记录：messages + supervised_message_indexes +
loss mask 信息。ExportAdapter 只负责把它序列化为具体训练格式并做往返验证。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TrainingRecordV4:
    sample_id: str
    mode: str
    render_profile_id: str
    messages: list[dict[str, str]]
    supervised_message_indexes: list[int]
    supervised_token_count: int
    protocol_snapshot_id: str
    system_anchor: str | None = None
    split: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "mode": self.mode,
            "render_profile_id": self.render_profile_id,
            "messages": self.messages,
            "supervised_message_indexes": self.supervised_message_indexes,
            "supervised_token_count": self.supervised_token_count,
            "protocol_snapshot_id": self.protocol_snapshot_id,
            "system_anchor": self.system_anchor,
            "split": self.split,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrainingRecordV4:
        return cls(
            sample_id=data["sample_id"],
            mode=data["mode"],
            render_profile_id=data["render_profile_id"],
            messages=data["messages"],
            supervised_message_indexes=data["supervised_message_indexes"],
            supervised_token_count=data["supervised_token_count"],
            protocol_snapshot_id=data["protocol_snapshot_id"],
            system_anchor=data.get("system_anchor"),
            split=data.get("split"),
        )


def estimate_token_count(messages: list[dict[str, str]], indexes: list[int]) -> int:
    """非 padding 监督 token 数估算：按中文字符数近似（每字约 1 token）。

    仅用于 manifest 与采样权重；训练时的真实 token 数以 LLaMA-Factory 为准。
    """
    return sum(len(messages[i]["content"]) for i in indexes)
