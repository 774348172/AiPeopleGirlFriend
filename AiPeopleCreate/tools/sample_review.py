# -*- coding: utf-8 -*-
"""分层采样审核（2026-08-10，"一劳永逸"方案第 3 步）。

替代全量人工审核：按风险级分层采样，配合校准后的 judge 自动拦截。

分层：
  低风险（casual/general/canon_qa/identity/quiet_company）  抽 10%
  中风险（romance/emotion/correction/vague/boundary）       抽 30%
  高风险（protective/supportive/safety）                    100%（G7 合同强制）

流程：
  1) judge 已在校准后自动拦低分样本（生成时 select_winner 淘汰 < 阈值候选）；
  2) 本工具对**已导出**（通过门禁+judge）的样本做分层抽样，输出抽检清单
     （build_review_page 格式的 sample_id 列表 + 所属风险级）；
  3) 人工只审抽中的样本；抽检通过率 ≥ 95% → 整批放行（SPC）。

用法:
  python tools/sample_review.py --data 训练数据/baiweixi_xxx.jsonl \
      --meta 训练数据/baiweixi_xxx.metadata.jsonl \
      --out 训练数据/xxx_sampling.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RISK_LOW = {"reply_casual", "reply_general", "reply_canon_qa", "reply_identity", "reply_quiet_company"}
RISK_MED = {"reply_romance", "reply_emotion", "reply_correction", "reply_vague", "reply_boundary"}
RISK_HIGH = {"reply_protective", "reply_supportive", "reply_safety"}

RATES = {"low": 0.10, "med": 0.30, "high": 1.0}


def risk_of(task_type: str) -> str:
    if task_type in RISK_LOW:
        return "low"
    if task_type in RISK_MED:
        return "med"
    return "high"


def _sample_hit(sample_id: str, rate: float) -> bool:
    """确定性抽样（同 sample_id 恒定，可复现）。"""
    digest = int.from_bytes(hashlib.sha256(sample_id.encode("utf-8")).digest()[:4], "big")
    return digest % 1000 < int(rate * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = [json.loads(l) for l in Path(args.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    metas = [json.loads(l) for l in Path(args.meta).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == len(metas), f"data {len(rows)} != meta {len(metas)}"

    sampled: list[dict] = []
    by_risk: Counter = Counter()
    for row, meta in zip(rows, metas):
        task = meta.get("task_type", "?")
        risk = risk_of(task)
        by_risk[risk] += 1
        if not _sample_hit(meta["sample_id"], RATES[risk]):
            continue
        conv = row.get("conversations", [])
        sampled.append({
            "sample_id": meta["sample_id"],
            "task_type": task,
            "risk": risk,
            "topic": meta.get("topic", ""),
            "conversations": conv,
        })

    out = Path(args.out)
    out.write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in sampled) + "\n",
        encoding="utf-8",
    )
    total = len(rows)
    print(f"整批 {total} 条 | 抽检 {len(sampled)} 条（{len(sampled)/total*100:.0f}%）")
    print(f"  风险分布: {dict(by_risk)}")
    print(f"  抽检分布: {dict(Counter(s['risk'] for s in sampled))}")
    print(f"抽检清单 → {out}")
    print("规则：抽检通过率 ≥95% 整批放行；否则补抽 + 定位问题类型")
    return 0


if __name__ == "__main__":
    sys.exit(main())
