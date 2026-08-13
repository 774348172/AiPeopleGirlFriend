"""sharegpt 数据适配器（M1 核心）。

把 life_corpus 的 JSONL（diary/chat/protective）转成 LLaMA-Factory 的 sharegpt 格式：
  {"conversations": [{"from": "human"|"gpt", "value": "..."}, ...]}

映射规则：
  - diary   → 1 条：human="写下你{date}的日记。" / gpt=<日记正文>
              （教模型"被问起那天的事时，能以本人口吻回忆并写出来"）
  - chat    → 多轮：other→human，person→gpt，按顺序展开
  - protective → 1 条：human=<越界提问> / gpt=<人格内的婉拒/转移>

用法:
  python -m data_gen.to_sharegpt --in data/life_corpus/m0.jsonl
  python -m data_gen.to_sharegpt --clean        # 只用通过规则检查的 .clean.jsonl
  python -m data_gen.to_sharegpt --in ... --out data/sft/aipeople_persona.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import ROOT

DIARY_PROMPT_TMPL = "写下你{date}那天的日记。"


def diary_to_sharegpt(sample: dict) -> dict:
    date = sample.get("date", "")
    return {
        "conversations": [
            {"from": "human", "value": DIARY_PROMPT_TMPL.format(date=date)},
            {"from": "gpt", "value": str(sample.get("content", "")).strip()},
        ]
    }


def chat_to_sharegpt(sample: dict) -> dict:
    turns = sample.get("content", [])
    if isinstance(turns, str):
        turns = json.loads(turns)
    convs = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        role = t.get("role")
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        side = "gpt" if role == "person" else "human"  # person=本人=gpt, 其余=human
        convs.append({"from": side, "value": text})
    # 合并连续同侧轮（sharegpt 期望交替；合并避免 LF 报错）
    merged = []
    for c in convs:
        if merged and merged[-1]["from"] == c["from"]:
            merged[-1]["value"] += "\n" + c["value"]
        else:
            merged.append(dict(c))
    return {"conversations": merged}


def protective_to_sharegpt(sample: dict) -> dict:
    content = sample.get("content", {})
    if isinstance(content, str):
        content = json.loads(content)
    return {
        "conversations": [
            {"from": "human", "value": str(content.get("question", "")).strip()},
            {"from": "gpt", "value": str(content.get("answer", "")).strip()},
        ]
    }


_CONVERTERS = {
    "diary": diary_to_sharegpt,
    "chat": chat_to_sharegpt,
    "protective": protective_to_sharegpt,
    "identity": protective_to_sharegpt,  # identity 和 protective 格式相同：{question, answer}
    "protective": protective_to_sharegpt,
}


def convert_file(in_path: Path, out_path: Path) -> tuple[int, int]:
    n_in = n_out = 0
    with open(in_path, "r", encoding="utf-8") as fin, open(out_path, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            n_in += 1
            sample = json.loads(line)
            conv = _CONVERTERS.get(sample.get("type"))
            if conv is None:
                continue
            try:
                rec = conv(sample)
            except Exception as e:  # noqa: BLE001
                print(f"[skip] {sample.get('id')}: {e}")
                continue
            if not rec["conversations"]:
                continue
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_out += 1
    return n_in, n_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/life_corpus/m0.jsonl")
    ap.add_argument("--out", default="data/sft/aipeople_persona.jsonl")
    ap.add_argument("--clean", action="store_true", help="改用 <in>.clean.jsonl（仅规则通过的样本）")
    args = ap.parse_args()

    in_path = ROOT / args.inp
    if args.clean:
        in_path = in_path.with_suffix(".clean.jsonl")
    if not in_path.exists():
        raise SystemExit(f"输入不存在: {in_path}")
    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_in, n_out = convert_file(in_path, out_path)
    print(f"转换: {n_in} 条样本 → {n_out} 条 sharegpt 样本")
    print(f"输出: {out_path}")
    print("\n样例（首条）:")
    first = out_path.read_text(encoding="utf-8").splitlines()[0]
    print(json.dumps(json.loads(first), ensure_ascii=False, indent=2)[:600])
    print(f"\n下一步: 在 LLaMA-Factory 的 data/dataset_info.json 注册 'aipeople_persona'，")
    print("        把该文件复制/软链到 LLaMA-Factory 的 data/ 目录，再跑训练配置。")


if __name__ == "__main__":
    main()
