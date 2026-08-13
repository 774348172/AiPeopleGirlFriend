# -*- coding: utf-8 -*-
"""judge 校准工具（阶段 3 C-4）：gold 集 → 校准报告。

G3 验收指标（v2 §11.2）：换序一致率、abstain 率、人机一致率（gold precision/recall）。

用法:
  python tools/calibrate_judge.py --gold judge_gold/gold_v1.jsonl
  python tools/calibrate_judge.py                          # 默认扫描 judge_gold/*.jsonl

gold 集格式（judge_gold/README.md）：
- 单候选条目：{"candidate": {sample_id/input/target}, "gold": {"scores": {axis: 0.0..1.0}}}
- 配对条目：   {"pair": [candidate_a, candidate_b], "gold": {"preferred": "a" | "b"}}

报告输出：
- 换序一致率（pairwise 换序两次一致占比，G3 阈值默认 0.7）
- abstain 率（judge 无法判断占比）
- 人机一致率（gold 偏好 vs judge 偏好）
- 每轴 MAE（gold scores vs judge scores）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_gen_v4.core.judge import LLMJudge, PairwiseJudge, SOFT_AXES  # noqa: E402


def _load_gold(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def calibrate(judge, rows: list[dict]) -> dict:
    report: dict = {"total": len(rows), "pairs": 0, "singles": 0}
    abstain = 0
    axis_errors: dict[str, list[float]] = {axis: [] for axis in SOFT_AXES}
    human_match = 0
    pair_agreements: list[bool] = []

    for row in rows:
        if "pair" in row:
            report["pairs"] += 1
            a, b = row["pair"][0], row["pair"][1]
            pairwise = PairwiseJudge(judge)
            preferred, agreed = pairwise.compare(a, b, {})
            pair_agreements.append(agreed)
            if not agreed:
                continue  # judge 不一致，该对不参与人机比较
            gold_pref = row.get("gold", {}).get("preferred")
            if gold_pref and preferred == gold_pref:
                human_match += 1
            if preferred is None:
                abstain += 1
        elif "candidate" in row:
            report["singles"] += 1
            result = judge.score(row["candidate"], {})
            if result.abstain:
                abstain += 1
                continue
            gold_scores = row.get("gold", {}).get("scores", {})
            for axis in SOFT_AXES:
                if axis in gold_scores:
                    axis_errors[axis].append(abs(gold_scores[axis] - result.scores.get(axis, 0.0)))

    report["abstain_rate"] = abstain / report["total"] if report["total"] else 0.0
    if pair_agreements:
        report["pairwise_agreement_rate"] = sum(pair_agreements) / len(pair_agreements)
    if report["pairs"]:
        report["human_agreement_rate"] = human_match / report["pairs"]
    report["axis_mae"] = {
        axis: (sum(errs) / len(errs) if errs else None)
        for axis, errs in axis_errors.items()
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", help="gold 集 jsonl 路径（缺省扫描 judge_gold/*.jsonl）")
    args = ap.parse_args()

    gold_paths = (
        [Path(args.gold)]
        if args.gold
        else sorted((ROOT / "judge_gold").glob("*.jsonl"))
    )
    gold_paths = [p for p in gold_paths if p.exists()]
    if not gold_paths:
        print("未找到 gold 集（judge_gold/*.jsonl）。参考 judge_gold/README.md 准备 gold 数据。")
        return 1

    # 真实 judge 需要模型池；此处用 ScriptedJudge 演示报告格式，
    # 接真实模型时替换为 LLMJudge（见 README）
    from data_gen_v4.core.judge import ScriptedJudge

    judge = ScriptedJudge()
    for path in gold_paths:
        rows = _load_gold(path)
        report = calibrate(judge, rows)
        print(f"\n== {path.name} ==")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
