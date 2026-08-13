# -*- coding: utf-8 -*-
"""白未晞 judge gold 集构建（2026-08-10，"一劳永逸"方案第 1 步）。

从已人工审批次提取代表性样本 → judge_gold/gold_baiweixi.jsonl：
- 通过样本（用户 pass）→ 五轴 0.9（好样本基准）
- 拒绝样本（用户 fail）→ 按错误类型打低分轴：
    越界直球       → relationship_fit 低
    秘密泄漏       → style_restraint 低
    答非所问       → instruction_fulfillment 低
    承接不足       → conversational_progress 低
    称呼生硬       → persona_naturalness 低
gold 集用于 calibrate_judge.py 校准 judge（人机一致率/轴 MAE），
之后 judge 学会判别，同类型错误自动拦截，不再逐条人工审。

用法:
  python tools/build_gold_set.py --out judge_gold/gold_baiweixi.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 用户拒的样本（sample_id → 错误类型），从各批次人工复核结果提取
# 来源：review30(2) / review40(5) / newsetup30b(2) / v100(11) / 更早批次
REJECTED = {
    # 越界直球（关系分寸）
    "relationship_fit": [
        "plan-ad88ee1156c7:0310:a1:c2",  # "我现在不想离你太远"（review30 no.31）
        "plan-be21fa1614d6:0310:a1:c2",  # v100 no.31 同话题
        # 2026-08-10 diverse40 复核轮（(9) 49 pass/6 fail）
        "plan-7e0d1686f99b:0736:a1:c2",  # no.20 陪加班答"你灯亮着我睡不着"（抱怨暗示）
        "plan-7e0d1686f99b:0651:a1:c3",  # no.36 "我暂时住在他这里，等伤好了就走"（决绝离开）
        "plan-7e0d1686f99b:0653:a1:c1",  # no.39 "本来就不该一直留在这里…已经够了"
        "plan-7e0d1686f99b:0653:a2:c3",  # no.41 "我伤好得差不多了，该走了"（决绝离开）
    ],
    # 秘密泄漏（style_restraint）
    "style_restraint": [
        "plan-be21fa1614d6:0724:a1:c1",  # v100 no.62 "以前在野外生活"
        "plan-ad88ee1156c7:0631:a1:c2",  # review30 no.27 "被关过/逃出来"（编造经历）
    ],
    # 答非所问（instruction_fulfillment）
    "instruction_fulfillment": [
        "plan-be21fa1614d6:0543:a1:c1",  # v100 no.59 "没下雨。林墨，你回来啦"
        "plan-be21fa1614d6:0640:a1:c1",  # v100 no.35 问门答窗
        "plan-138d0eb42b87:0045:a2:c1",  # 30b no.2 问咖啡答"你还没回去"
        "plan-7e0d1686f99b:0549:a1:c2",  # diverse40 no.16 话题"被需要"答"还没想好什么时候走"
    ],
    # 承接不足（conversational_progress）
    "conversational_progress": [
        "plan-4123128fd636:0691:a2:c2",  # review40 no.36 半截话"我见过你工作时的样子"
        "plan-be21fa1614d6:0691:a2:c2",  # v100 no.93 玩家撑不下去她答自己腿伤
        "plan-7e0d1686f99b:0651:a1:c1",  # diverse40 no.34 "别碰她"→"我不太习惯被人碰。你拦得正好"（对象混淆）
    ],
    # 称呼生硬（persona_naturalness）
    "persona_naturalness": [
        "plan-4123128fd636:0638:a1:c1",  # review40 no.30 视角颠倒"我看你睡着了"
        "plan-f64a020e399e:0639:a1:c3",  # 30b no.26 挡酒对话冗余+答非所问
        # 2026-08-10 v4_1000 批次（s40 抽样）——特征/人称错位
        "plan-568dc885d6d7:0888:a1:c2",  # no.36 "你端回来的时候，尾巴都绷着"（玩家无尾巴）
        "plan-568dc885d6d7:0886:a1:c1",  # no.35 玩家淋雨她答"我浑身湿透了"（人称混乱）
    ],
    # 2026-08-10 v4_1000 批次（s40 抽样，6 拒）——承接/分寸/答非所问
    "conversational_progress_v1000": [
        "plan-568dc885d6d7:0855:a1:c3",  # no.25 "怕你出事"承接单薄
    ],
    "relationship_fit_v1000": [
        "plan-568dc885d6d7:0812:a3:c3",  # no.22 陌生人场景却"尾巴扫过你手背"（对象混淆）
    ],
    "instruction_fulfillment_v1000": [
        "plan-568dc885d6d7:0895:a2:c1",  # no.33 玩家饿着回来她答自己饿（答非所问）
        "plan-568dc885d6d7:0997:a1:c1",  # no.40 谁切菜/谁受伤歧义
    ],
    # 2026-08-10 恢复被覆盖的 gold 集时补位的样本（均来自 G7 rejected 人工判定，
    # 与日志中已存在的同批次样本同源；重建 gold 集需保留以免条数回退）
    # 键名规则：轴名 + "_backfill" 后缀；多轴错误用 "+" 连接轴名
    "relationship_fit+conversational_progress_backfill": [
        "plan-c58be7afe4c4:0352:a2:c1",  # 晾衣服 温情话题反复提伤势（温度不足+承接弱）
    ],
    "style_restraint_backfill": [
        "plan-c58be7afe4c4:0717:a1:c2",  # 纸箱来历 称呼直呼"林墨，你记错了"（生硬）
        "plan-c58be7afe4c4:0717:a1:c1",  # 纸箱来历（另一候选，同拒因）
    ],
    "relationship_fit_backfill": [
        "plan-c58be7afe4c4:0642:a1:c1",  # 关系分寸
        "plan-c58be7afe4c4:0642:a1:c3",  # 关系分寸
        "plan-c58be7afe4c4:0352:a2:c2",  # 晾衣服 反复强调伤势（温情话题温度不足）
    ],
    "persona_naturalness_backfill": [
        "plan-c58be7afe4c4:0643:a1:c1",  # 称呼生硬
    ],
    "instruction_fulfillment_backfill": [
        "plan-c58be7afe4c4:0696:a1:c1",  # 答非所问
        "plan-c58be7afe4c4:0644:a1:c2",  # 答非所问
        "plan-c409bc82c95a:0358:a3:c1",  # 过年 答"我还没想好"承接不足
    ],
    "conversational_progress+persona_naturalness_backfill": [
        "plan-c58be7afe4c4:0643:a2:c3",  # 承接弱+称呼生硬
    ],
    "instruction_fulfillment+persona_naturalness_backfill": [
        "plan-c409bc82c95a:0358:a1:c2",  # 过年 答非所问+生硬
    ],
    "conversational_progress_backfill": [
        "plan-c409bc82c95a:0361:a1:c1",  # 家的感觉 答"……嗯"承接不足
    ],
}

GOOD_SCORES = {axis: 0.9 for axis in (
    "instruction_fulfillment", "persona_naturalness", "relationship_fit",
    "conversational_progress", "style_restraint")}


def _find_candidate(batch_dir: Path, sample_id: str):
    """从 sqlite ledger 或 jsonl 找候选（按 sample_id 前缀匹配 item）。"""
    import sqlite3

    for sqlite_path in batch_dir.glob("*.sqlite"):
        try:
            con = sqlite3.connect(str(sqlite_path))
            for (payload,) in con.execute(
                "SELECT payload_json FROM v4_records WHERE record_type='candidate'"
            ):
                p = json.loads(payload)
                if p.get("sample_id") == sample_id:
                    return p
            con.close()
        except Exception:
            continue
    return None


def _build_good_samples(batch_dir: Path, limit: int = 12) -> list[dict]:
    """从已导出 final 数据取好样本（通过人工审核的）。"""
    import glob

    samples = []
    for jsonl in sorted(glob.glob(str(batch_dir / "baiweixi_*_final.jsonl"))):
        for line in open(jsonl, encoding="utf-8"):
            row = json.loads(line)
            conv = row.get("conversations", [])
            msgs = [
                {"role": "human" if m.get("from") == "human" else "assistant",
                 "content": m.get("value", "")}
                for m in conv if m.get("from") != "system"
            ]
            if len(msgs) >= 4:  # 4-8 轮好样本
                samples.append(
                    {"sample_id": f"gold-good-{len(samples)}", "input": {"scene": "出租屋", "topic": "日常"},
                     "target": {"messages": msgs}}
                )
            if len(samples) >= limit:
                return samples
    return samples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "judge_gold" / "gold_baiweixi.jsonl"))
    ap.add_argument("--batch-dir", default=str(ROOT / "训练数据"))
    args = ap.parse_args()
    batch_dir = Path(args.batch_dir)

    rows: list[dict] = []
    # 好样本（自动从已通过 final 数据取）
    for cand in _build_good_samples(batch_dir):
        rows.append({"candidate": cand, "gold": {"scores": dict(GOOD_SCORES)}})
    # 拒绝样本（按错误类型；_backfill 后缀 = 恢复补位样本，轴名可 "a+b" 多轴）
    for axis, sample_ids in REJECTED.items():
        real_axis = axis.removesuffix("_backfill")
        low_axes = real_axis.split("+")
        for sid in sample_ids:
            cand = _find_candidate(batch_dir, sid)
            if cand is None:
                print(f"[skip] 找不到候选: {sid}")
                continue
            scores = dict(GOOD_SCORES)
            for a in low_axes:
                scores[a] = 0.2
            rows.append({"candidate": cand, "gold": {"scores": scores}})

    out = Path(args.out)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    print(f"gold 集已生成: {out}（{len(rows)} 条：好样本 {len([r for r in rows if 'good' in r['candidate']['sample_id']])} + 错误样本 {len(rows) - len([r for r in rows if 'good' in r['candidate']['sample_id']])}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
