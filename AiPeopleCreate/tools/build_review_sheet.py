# -*- coding: utf-8 -*-
"""人工复核表生成（2026-08-06 T12 初版；2026-08-09 多角色参数化）。

对生成的训练数据（jsonl + metadata 旁路）生成 Markdown 复核清单：
- review_requirement=full 的类（supportive/protective/safety 等）全量列出；
- 其余类型全部列出（量小）。

复核表格式（Markdown，供人工逐条打勾）：
| # | 类型 | 话题 | 玩家 | 角色回复摘要 | 复核 | 备注 |
复核要点：承接/严肃不调侃/不编经历/边界/口癖/秘密词（按角色可配置）。

用法:
  python tools/build_review_sheet.py                              # 默认秦（历史行为）
  python tools/build_review_sheet.py --character baiweixi \
      --data 训练数据/baiweixi_v4_20.jsonl \
      --meta 训练数据/baiweixi_v4_20.metadata.jsonl \
      --out 设计文档/复核表_白未晞_v1.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 角色 → 复核要点（秘密词按角色配置；默认秦历史行为）
CHARACTER_REVIEW_TIPS = {
    "qinweixi": (
        "复核要点：① 是否准确承接玩家核心信息；② 严肃场景是否暂停调侃；"
        "③ 是否编造共同经历/前情；④ 关系边界是否越级；⑤ 口癖密度（开头哼/喂/啧）；"
        "⑥ 秘密词（地堡/裂缝/异世界等）；⑦ 是否朗读身份属性。"
    ),
    "baiweixi": (
        "复核要点：① 是否准确承接玩家核心信息；② 严肃场景是否暂停调侃；"
        "③ 是否编造共同经历/前情；④ 关系边界（救助-暂住-未确认恋爱）是否越级；"
        "⑤ 口癖密度（喵/哼/喂/啧，白未晞无口癖设定应接近 0）；"
        "⑥ 秘密词（妖果/上古传承/深山/上海）；"
        "⑦ 是否用妖力感知编造现实信息（天气/温度/位置）；⑧ 人称是否混乱（把她的姿态安到玩家身上）。"
    ),
}

DEFAULT_DATA = ROOT / "训练数据/qin_v4_t12_matrix.jsonl"
DEFAULT_OUT = ROOT / "设计文档/复核表_T12行为矩阵_v1.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--character", default="qinweixi",
                    help="角色 id（默认 qinweixi 历史行为；baiweixi 用白未晞复核要点）")
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="训练 jsonl")
    ap.add_argument("--meta", default=None, help="metadata.jsonl（缺省 = data 同名 .metadata.jsonl）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="复核表输出路径")
    ap.add_argument("--ledger", default=None,
                    help="ledger sqlite（可选）：把 G7 待审的 full 类候选也列入复核表"
                         "（2026-08-09：候选未被导出，需人工审核记录才能放行）")
    args = ap.parse_args()

    data_path = Path(args.data)
    meta_path = Path(args.meta) if args.meta else data_path.with_suffix(".metadata.jsonl")
    out_path = Path(args.out)
    tips = CHARACTER_REVIEW_TIPS.get(args.character, CHARACTER_REVIEW_TIPS["qinweixi"])

    rows = [json.loads(l) for l in data_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = [json.loads(l) for l in meta_path.read_text(encoding="utf-8").splitlines() if l.strip()] if meta_path.exists() else []
    assert len(rows) == len(meta), f"训练行 {len(rows)} != 元数据 {len(meta)}"

    # 2026-08-09：从 ledger 读 G7 待审候选（full 类未导出，须人工复核记录才能放行）
    ledger_rows: list[dict] = []
    if args.ledger:
        import sqlite3

        full_tasks = ("reply_protective", "reply_supportive", "reply_safety")
        con = sqlite3.connect(str(args.ledger))
        try:
            for (payload,) in con.execute(
                "SELECT payload_json FROM v4_records WHERE record_type='candidate'"
            ):
                p = json.loads(payload)
                if p.get("task_type") not in full_tasks:
                    continue  # 只列 full 类（G7 强制人工审核）
                target = p.get("target") or {}
                messages = target.get("messages") or []
                if not messages:
                    continue
                ledger_rows.append(
                    {
                        "sample_id": p.get("sample_id", ""),
                        "task_type": p.get("task_type", "?"),
                        "topic": (p.get("input") or {}).get("topic", ""),
                        "human": " ".join(
                            m["content"] for m in messages if m.get("role") == "human"
                        ),
                        "assistant": " ".join(
                            m["content"] for m in messages if m.get("role") == "assistant"
                        ),
                        "pending_g7": True,
                    }
                )
        finally:
            con.close()

    lines = [
        f"# {args.character} 人工复核表",
        "",
        f"> 生成：{len(rows)} 条导出 + {len(ledger_rows)} 条 G7 待审 | 复核要求：严肃支持 100% 人工，其余全量抽查",
        f"> {tips}",
        "",
        "| # | 类型 | 话题 | 玩家台词 | 角色回复（节选） | 复核 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, (row, m) in enumerate(zip(rows, meta), start=1):
        msgs = row["conversations"]
        humans = [x["value"] for x in msgs if x["from"] == "human"]
        assists = [x["value"] for x in msgs if x["from"] == "gpt"]
        human_text = (humans[0][:40] + "…") if humans and len(humans[0]) > 40 else (humans[0] if humans else "")
        assist_text = (assists[0][:60] + "…") if assists and len(assists[0]) > 60 else (assists[0] if assists else "")
        human_text = human_text.replace("|", "｜").replace("\n", " ")
        assist_text = assist_text.replace("|", "｜").replace("\n", " ")
        lines.append(
            f"| {i} | {m.get('task_type','?')} | {m.get('topic','')[:18]} | {human_text} | {assist_text} | ☐ | |"
        )
    # G7 待审候选（未导出，须人工审核后放行）
    for j, row in enumerate(ledger_rows, start=len(rows) + 1):
        human_text = (row["human"][:40] + "…") if len(row["human"]) > 40 else row["human"]
        assist_text = (row["assistant"][:60] + "…") if len(row["assistant"]) > 60 else row["assistant"]
        human_text = human_text.replace("|", "｜").replace("\n", " ")
        assist_text = assist_text.replace("|", "｜").replace("\n", " ")
        lines.append(
            f"| {j} | {row['task_type']}（G7待审） | {row['topic'][:14]} | {human_text} | {assist_text} | ☐ | |"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"复核表 → {out_path}（{len(rows)} 导出 + {len(ledger_rows)} G7 待审，character={args.character}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
