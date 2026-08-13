"""FREEZE-02 训练合同准入（P0-5，阶段 1 施工块）。

DatasetGenerator Seam 在生成前按冻结合同检查 mode：未冻结 mode 返回
AdmissionBlocked（含 reason/contract_ref/next_checkpoint），不产出数据。

契约输入：freeze02_contract_v2.json（AI 程序侧 eval/training_contract，只读引用，
路径经配置注入；测试用 fixture 契约）。准入语义（freeze02_contract_v2.json）：
- data_admission == "allowed_after_dataset_freeze"：数据集冻结后可生产；
  visible_reply.first_candidate_modes 限定首个 ~1000 条工程候选只允许 REPLY。
- data_admission == "blocked_until_*"：对应冻结前置（mode schema / SELECT-01）
  完成前禁止生产或混训。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class AdmissionBlocked:
    """mode 被冻结合同拒绝生产。contract_ref 指向契约来源（路径或 <inline>）。"""

    mode: str
    reason_codes: list[str] = field(default_factory=list)
    contract_id: str = ""
    contract_ref: str = ""
    next_checkpoint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reason_codes": list(self.reason_codes),
            "contract_id": self.contract_id,
            "contract_ref": self.contract_ref,
            "next_checkpoint": self.next_checkpoint,
        }


class Freeze02Admission:
    """按 FREEZE-02 冻结合同做 mode 准入检查（admit 返回 None = 允许生产）。"""

    def __init__(
        self,
        contract: dict[str, Any] | None = None,
        contract_path: str | None = None,
    ) -> None:
        if contract is not None:
            self._contract = contract
            self._source = "<inline>"
        elif contract_path is not None:
            self._source = contract_path
            self._contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
        else:
            raise ValueError("Freeze02Admission 需要 contract 或 contract_path")
        self._families = self._contract.get("dataset_families", [])
        self._contract_id = str(self._contract.get("contract_id", ""))
        self._next_checkpoint = str(self._contract.get("next_checkpoint", ""))

    def admit(
        self, mode: str, *, first_candidate_batch: bool = True
    ) -> AdmissionBlocked | None:
        """mode 准入：None = allowed；否则返回 AdmissionBlocked（未冻结/非首个候选）。"""
        family = next(
            (f for f in self._families if mode in f.get("allowed_modes", [])), None
        )
        if family is None:
            return self._blocked(mode, "mode_not_in_any_family")
        admission = family.get("data_admission", "")
        if admission.startswith("blocked_until_"):
            return self._blocked(mode, f"blocked:{admission}")
        if admission == "allowed_after_dataset_freeze" and first_candidate_batch:
            first = family.get("first_candidate_modes") or []
            if mode not in first:
                return self._blocked(mode, "not_first_candidate_mode")
        return None

    def _blocked(self, mode: str, *reason_codes: str) -> AdmissionBlocked:
        return AdmissionBlocked(
            mode=mode,
            reason_codes=list(reason_codes),
            contract_id=self._contract_id,
            contract_ref=self._source,
            next_checkpoint=self._next_checkpoint,
        )
