# -*- coding: utf-8 -*-
"""评测 delta 工具（阶段 5 E-2）。

消费 diagnostic120 paired 结果 / 人工盲测结果 → 晋升判定输入（eval delta 报告）。

用法:
  python tools/eval_delta.py --pairs eval/diagnostic120/reports/simple120-v1/pairs.jsonl
                            --cases eval/diagnostic120/reports/simple120-v1/results.jsonl
  python tools/eval_delta.py --seed-deltas '{"42": {...}, "2026": {...}, "777": {...}}'
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.core.eval_metrics import (  # noqa: E402
    group_delta,
    misrefusal_rate,
    parse_paired_delta,
    seed_consistency,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", help="paired delta 行列表（improved/regressed/unchanged + category）")
    ap.add_argument("--cases", help="diagnostic120 case 输出行列表（误拒率）")
    ap.add_argument("--seed-deltas", help="JSON：{seed: {improved, regressed, unchanged}}")
    args = ap.parse_args()

    report: dict = {}
    if args.pairs:
        pairs = [
            json.loads(line)
            for line in Path(args.pairs).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        summary = parse_paired_delta(pairs)
        report["paired_delta"] = {
            "improved": summary.improved,
            "regressed": summary.regressed,
            "unchanged": summary.unchanged,
            "groups": group_delta(summary),
        }
    if args.cases:
        cases = [
            json.loads(line)
            for line in Path(args.cases).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        report["misrefusal_rate"] = misrefusal_rate(cases)
    if args.seed_deltas:
        seed_deltas = json.loads(args.seed_deltas)
        report["seed_consistency"] = seed_consistency(list(seed_deltas.values()))
        report["seed_deltas"] = seed_deltas
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
