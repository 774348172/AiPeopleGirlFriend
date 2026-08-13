"""失败回流（阶段 5 E-4：behavior_gap 登记）。

v2 §5.3 / §10 阶段 5：失败只回流为 `behavior_gap_id + evidence + model_delta`，
**不直接新增关键词规则**——登记即停，处置决策由人/蓝图流程做（改来源、
蓝图、生成策略或选择器，先关联 gap/候选/训练版本/评测 delta）。

- GapRegistry：behavior_gap 登记（幂等，按 gap 指纹去重）；
- 从 ledger 提取证据：FailureRecord（error_code/reason）→ taxonomy E1-E7 →
  聚合为候选 gap（半自动，供人确认登记）。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GAP_STATUSES = ("open", "addressed", "wontfix")
GAP_SOURCES = ("taxonomy", "failure", "eval_delta")


@dataclass(frozen=True, slots=True)
class BehaviorGap:
    gap_id: str
    source: str
    family_id: str
    evidence: list[str]
    model_delta: dict[str, Any] = field(default_factory=dict)
    status: str = "open"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "source": self.source,
            "family_id": self.family_id,
            "evidence": list(self.evidence),
            "model_delta": dict(self.model_delta),
            "status": self.status,
            "created_at": self.created_at,
        }


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def gap_fingerprint(source: str, family_id: str, evidence: list[str]) -> str:
    body = json.dumps([source, family_id, sorted(evidence)], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def build_gap(
    *,
    source: str,
    family_id: str,
    evidence: list[str],
    model_delta: dict[str, Any] | None = None,
    status: str = "open",
) -> BehaviorGap:
    """构造 gap（gap_id 由指纹派生：同 source+family+evidence 幂等）。"""
    if source not in GAP_SOURCES:
        raise ValueError(f"非法 gap source: {source}")
    if status not in GAP_STATUSES:
        raise ValueError(f"非法 gap status: {status}")
    fingerprint = gap_fingerprint(source, family_id, evidence)
    return BehaviorGap(
        gap_id=f"bg:{family_id}:{fingerprint}",
        source=source,
        family_id=family_id,
        evidence=list(evidence),
        model_delta=model_delta or {},
        status=status,
        created_at=_now(),
    )


class GapRegistry:
    """behavior_gap 登记表（JSONL 持久化；幂等：同 gap_id 不重复登记）。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._gaps: dict[str, BehaviorGap] = {}
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                self._gaps[data["gap_id"]] = BehaviorGap(**data)

    def register(self, gap: BehaviorGap) -> bool:
        """登记 gap；已存在（同 gap_id）→ 返回 False（幂等）。"""
        if gap.gap_id in self._gaps:
            return False
        self._gaps[gap.gap_id] = gap
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(gap.to_dict(), ensure_ascii=False) + "\n")
        return True

    def all(self) -> list[BehaviorGap]:
        return list(self._gaps.values())

    def open_gaps(self) -> list[BehaviorGap]:
        return [g for g in self._gaps.values() if g.status == "open"]


def extract_gap_candidates(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 ledger FailureRecord 提取候选 gap（半自动，供人确认）。

    failures 元素：{record_id, error_code, reason, family_id?, task_type?}。
    按 (error_code, taxonomy 归类) 聚合 → 候选 gap（不自动登记，不产出关键词规则）。
    """
    from tools.taxonomy_report import classify

    candidates: dict[str, dict[str, Any]] = {}
    for failure in failures:
        reason = str(failure.get("reason", ""))
        taxonomy = classify(reason)
        key = f"{failure.get('error_code', 'unknown')}:{taxonomy}"
        entry = candidates.setdefault(
            key,
            {
                "error_code": failure.get("error_code", "unknown"),
                "taxonomy": taxonomy,
                "family_id": failure.get("family_id") or failure.get("task_type") or "unknown",
                "evidence": [],
                "count": 0,
            },
        )
        entry["evidence"].append(str(failure.get("record_id", "?")))
        entry["count"] += 1
    return list(candidates.values())
