# -*- coding: utf-8 -*-
"""人工审核结果导入 ledger（G7 放行闭环，阶段 1 块 1.4 缺口补全）。

复核流程：生成 → 人工复核（tools/build_review_sheet.py 复核表）→ 结果文件 →
本工具把 reviewer 记录写进 ledger（GateDecisionRecord）→ G7 对 full 任务放行。

结果文件格式（每行 JSON）：
  {"sample_id": "plan-xxx:0000:a1:c1", "reviewer": "human-1",
   "decision": "approved" | "rejected", "note": "可选"}

用法:
  python tools/apply_review.py --ledger 训练数据/qin_v4_1000_final.sqlite \
      --reviews 复核结果.jsonl
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.core.records import GateDecisionRecord, LineageHeaderV4, record_from_dict  # noqa: E402
from data_gen_v4.core.sink import AppendSink  # noqa: E402


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def load_reviews(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        data = json.loads(line)
        if isinstance(data, list):
            rows.extend(data)  # 兼容单行 JSON 数组
        else:
            rows.append(data)
    return rows


def find_candidate(con: sqlite3.Connection, sample_id: str) -> dict | None:
    for (payload,) in con.execute(
        "SELECT payload_json FROM v4_records WHERE record_type='candidate'"
    ):
        data = json.loads(payload)
        if data.get("sample_id") == sample_id:
            return data
    return None


def apply_reviews(ledger_path: str | Path, reviews: list[dict]) -> dict:
    """导入复核结果。返回统计 {applied, skipped, missing, rejected_records}。

    幂等：该 sample_id 已有 G7 复核记录 → 跳过（不重复写入）。
    """
    stats = {"applied": 0, "skipped": 0, "missing": 0, "rejected_records": 0}
    con = sqlite3.connect(str(ledger_path))
    try:
        # 已存在的 G7 复核记录（幂等判定）
        reviewed_samples: set[str] = set()
        for (payload,) in con.execute(
            "SELECT payload_json FROM v4_records WHERE record_type='gate_decision'"
        ):
            data = json.loads(payload)
            if data.get("gate_id") == "G7":
                reviewed_samples.add(data.get("subject_sample_id", ""))
        for review in reviews:
            sample_id = review.get("sample_id")
            reviewer = review.get("reviewer")
            decision = review.get("decision")
            if not sample_id or not reviewer or decision not in ("approved", "rejected"):
                stats["skipped"] += 1
                continue
            if sample_id in reviewed_samples:
                stats["skipped"] += 1  # 幂等：已导入过
                continue
            candidate = find_candidate(con, sample_id)
            if candidate is None:
                stats["missing"] += 1
                print(f"  [missing] 候选不存在: {sample_id}")
                continue
            header = LineageHeaderV4.from_dict(candidate)
            record = GateDecisionRecord(
                header=header.with_record(record_type="gate_decision"),
                subject_sample_id=sample_id,
                subject_candidate_record_id=candidate["record_id"],
                gate_id="G7",
                decision=decision,
                reason_codes=[str(review.get("note", ""))] if review.get("note") else [],
                validator_id="human-review",
                reviewer=reviewer,
                reviewed_at=_now(),
            )
            with AppendSink.open(ledger_path) as sink:
                created = sink.append(
                    record,
                    json.dumps(
                        [header.run_id, header.plan_id, "review", sample_id],
                        separators=(",", ":"),
                    ),
                )
            if created:
                stats["applied"] += 1
                reviewed_samples.add(sample_id)
            else:
                stats["skipped"] += 1
            if decision == "rejected":
                stats["rejected_records"] += 1
    finally:
        con.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True, help="ledger sqlite（候选记录 + GateDecision 落盘）")
    ap.add_argument("--reviews", required=True, help="复核结果 jsonl")
    args = ap.parse_args()

    reviews = load_reviews(args.reviews)
    if not reviews:
        print("[error] 复核结果为空")
        return 1
    stats = apply_reviews(args.ledger, reviews)
    print(
        f"导入完成: applied={stats['applied']} skipped={stats['skipped']} "
        f"missing={stats['missing']}（rejected 记录 {stats['rejected_records']} 条）"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
