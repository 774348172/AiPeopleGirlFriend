# -*- coding: utf-8 -*-
"""晋升判定工具（阶段 5 E-3）。

输入：评测/去重/切分/manifest/盲测证据 JSON → 晋升条件 7 条逐条判定 → 报告。

用法:
  python tools/promotion_check.py --evidence promotion_evidence.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.core.promotion import PromotionGate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True, help="晋升证据 JSON（各条件输入）")
    args = ap.parse_args()

    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    report = PromotionGate().evaluate(evidence)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.promoted else 1


if __name__ == "__main__":
    sys.exit(main())
