"""Haruhi 数据 → sharegpt 转换器。

把 ChatHaruhi 54K 里指定角色（如于谦）的样本转成 LLaMA-Factory sharegpt 格式。
支持单轮（user_question→agent_response）和多轮（more_dialogues 展开）。

用法:
  python -m data_gen.haruhi_to_sharegpt --in F:/AiPeople/GithubData/chat/Haruhi_54K_v1.jsonl --role 于谦 --out data/sft/yuqian_haruhi.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import ROOT


def convert_sample(d: dict) -> dict | None:
    """转一条 Haruhi 样本为 sharegpt。返回 None 则跳过。"""
    convs = []

    # 首轮
    q = str(d.get("user_question", "")).strip()
    a = str(d.get("agent_response", "")).strip()
    if not q or not a:
        return None
    convs.append({"from": "human", "value": q})
    convs.append({"from": "gpt", "value": a})

    # 多轮（more_dialogues）——格式为 "角色:「文本」" 字符串
    for md in d.get("more_dialogues", []):
        if isinstance(md, dict):
            role = md.get("role", "")
            text = str(md.get("text", md.get("content", ""))).strip()
        elif isinstance(md, str):
            # 解析 "角色:「文本」" 格式
            if ":" in md:
                colon = md.index(":")
                role = md[:colon].strip()
                text = md[colon + 1:].strip().strip("「」\"'").strip()
            else:
                role = ""
                text = md.strip()
        else:
            continue
        if not text:
            continue
        # 于谦 = gpt，其余 = human
        side = "gpt" if "于谦" in role or role == "agent" else "human"
        # 合并连续同侧
        if convs and convs[-1]["from"] == side:
            convs[-1]["value"] += "\n" + text
        else:
            convs.append({"from": side, "value": text})

    return {"conversations": convs}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="Haruhi JSONL 路径")
    ap.add_argument("--role", required=True, help="角色名（如 于谦）")
    ap.add_argument("--out", required=True, help="输出 sharegpt JSONL 路径")
    args = ap.parse_args()

    in_path = Path(args.inp)
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = in_path.read_text(encoding="utf-8").splitlines()
    n_total = n_out = n_multi = 0

    with open(out_path, "w", encoding="utf-8") as f:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get("agent_role") != args.role:
                continue
            n_total += 1
            rec = convert_sample(d)
            if rec is None:
                continue
            if len(rec["conversations"]) > 2:
                n_multi += 1
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_out += 1

    print(f"角色={args.role} | 总样本={n_total} | 转换={n_out} | 多轮={n_multi}")
    print(f"输出: {out_path}")
    # 样例
    first = out_path.read_text(encoding="utf-8").splitlines()[0]
    print(f"\n首条样例:")
    print(json.dumps(json.loads(first), ensure_ascii=False, indent=2)[:300])


if __name__ == "__main__":
    main()
