# -*- coding: utf-8 -*-
"""Evol 演化器（阶段 3 C-6）：从行为族蓝图演化场景/证据状态/表达方式/难度变体。

Evol 约束（v2 §7.2）：只演化 scene/evidence_state/expression/difficulty，
**禁止演化 canon**——输出条目只含场景描述与标签，不产生任何事实内容。

用法:
  python tools/evolve_blueprint.py                      # 预演（打印变体）
  python tools/evolve_blueprint.py --out evolve_v1.jsonl   # 输出供人工确认

输出条目（人工确认后进 pools，不自动改）：
  {"task_type": ..., "family": ..., "scene": ..., "difficulty": ...,
   "evolved_from": ..., "canon_touched": false}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.core.blueprint import load_blueprint  # noqa: E402

BLUEPRINT_PATH = ROOT / "profiles" / "qinweixi" / "blueprint.yaml"

# 表达方式演化（通用，不涉及角色 canon）
EXPRESSION_VARIANTS = {
    "casual": "口吻更随意",
    "tender": "口吻更温柔",
    "playful": "口吻更俏皮",
    "calm": "口吻更沉稳",
    "brief": "回复更简短",
}


def evolve(blueprint) -> list[dict]:
    rows: list[dict] = []
    for family_id, fam in blueprint.behavior_families.items():
        if not fam.get("task_types"):
            continue
        for level in ("easy", "medium", "hard"):
            gradients = (fam.get("difficulty") or {}).get(level) or []
            for i, template in enumerate(gradients):
                for expr_name in ("casual", "tender"):
                    rows.append(
                        {
                            "task_type": fam["task_types"][0],
                            "family": family_id,
                            "scene": template,
                            "evidence_state": (fam.get("evidence_states") or [""])[0],
                            "expression": expr_name,
                            "difficulty": level,
                            "evolved_from": f"{family_id}:{level}:{i}",
                            "canon_touched": False,
                        }
                    )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="输出 jsonl 路径（缺省仅打印）")
    args = ap.parse_args()

    blueprint = load_blueprint(BLUEPRINT_PATH)
    rows = evolve(blueprint)
    print(f"蓝图 {blueprint.blueprint_id}：八族 Evol 预演 {len(rows)} 条变体")
    print(f"Evol 约束 forbid: {blueprint.evol_constraints.get('forbid')}")
    for row in rows[:5]:
        print(" ", row["family"], row["difficulty"], "|", row["scene"][:24])
    if args.out:
        out = Path(args.out)
        with out.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"输出 → {out}（人工确认后进 pools；不自动改 pools）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
