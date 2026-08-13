# -*- coding: utf-8 -*-
"""behavior_gap 登记工具（阶段 5 E-4）。

用法:
  python tools/register_behavior_gap.py --ledger 训练数据/qin_v4_1000_final.sqlite \
      --registry 设计文档/通用数据生成器/施工/behavior_gaps.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.core.behavior_gap import GapRegistry, build_gap, extract_gap_candidates  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", help="ledger sqlite（FailureRecord 提取）")
    ap.add_argument("--registry", required=True, help="gap 登记表 jsonl 路径")
    ap.add_argument("--source", default="failure", choices=["failure", "eval_delta", "taxonomy"])
    args = ap.parse_args()

    registry = GapRegistry(args.registry)
    if args.ledger:
        con = sqlite3.connect(args.ledger)
        failures = []
        for (payload,) in con.execute(
            "SELECT payload_json FROM v4_records WHERE record_type='failure'"
        ):
            data = json.loads(payload)
            failures.append(
                {
                    "record_id": data.get("record_id"),
                    "error_code": data.get("error_code"),
                    "reason": data.get("reason"),
                    "family_id": data.get("family_id"),
                    "task_type": data.get("task_type"),
                }
            )
        candidates = extract_gap_candidates(failures)
        print(f"从 ledger 提取 {len(failures)} 条失败 → {len(candidates)} 个候选 gap")
        registered = 0
        for cand in candidates:
            gap = build_gap(
                source=args.source,
                family_id=cand["family_id"],
                evidence=cand["evidence"][:5],
                model_delta={"error_code": cand["error_code"], "taxonomy": cand["taxonomy"]},
            )
            if registry.register(gap):
                registered += 1
        print(f"登记 {registered} 个 gap（幂等，重复自动跳过）→ {args.registry}")
        print(f"当前 open gaps: {len(registry.open_gaps())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
