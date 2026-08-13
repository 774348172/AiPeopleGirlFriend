"""行为族蓝图（阶段 3 C-6，v2 审核报告 §7.2）。

- 蓝图声明八族（fact_use/memory_boundary/visibility/capability/serious_support/
  relationship_boundary/proactive/background_structured），每族映射 task_types +
  场景模板 + 证据状态 + 难度梯度；
- 校验器：recipe strata 必须全部归族（缺族 → 编译期 PreconditionFailedError）；
- Evol 约束：只演化 scene/evidence_state/expression/difficulty，禁止演化 canon。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

EVOL_ALLOWED = ("scene", "evidence_state", "expression", "difficulty")


@dataclass(frozen=True, slots=True)
class Blueprint:
    blueprint_id: str
    behavior_families: dict[str, dict[str, Any]]
    evol_constraints: dict[str, Any] = field(default_factory=dict)

    @property
    def family_task_types(self) -> dict[str, set[str]]:
        return {
            fid: set((fam.get("task_types") or []))
            for fid, fam in self.behavior_families.items()
        }

    def family_of(self, task_type: str) -> str | None:
        for fid, tasks in self.family_task_types.items():
            if task_type in tasks:
                return fid
        return None

    def validate_family_coverage(self, strata: list[str]) -> list[str]:
        """未归族的 task_type 列表（空 = 全覆盖）。"""
        return [s for s in strata if self.family_of(s) is None]

    def validate_evol_constraints(self) -> list[str]:
        """Evol 约束检查：禁止演化 canon（forbid 含 canon 时合法）。"""
        forbidden = self.evol_constraints.get("forbid") or []
        if "canon" not in forbidden:
            return ["evol_constraints 必须禁止演化 canon"]
        return []

    def difficulty_gradient(self, family_id: str, level: str) -> list[str]:
        fam = self.behavior_families.get(family_id) or {}
        return list((fam.get("difficulty") or {}).get(level) or [])


def load_blueprint(path: str | Path) -> Blueprint:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Blueprint(
        blueprint_id=str(doc.get("blueprint_id", "?")),
        behavior_families=doc.get("behavior_families") or {},
        evol_constraints=doc.get("evol_constraints") or {},
    )
