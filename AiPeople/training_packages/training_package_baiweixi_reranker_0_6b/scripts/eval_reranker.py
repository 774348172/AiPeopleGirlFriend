# -*- coding: utf-8 -*-
"""Reranker 验证脚本：dev 集准确率 + 真实场景推理演示（2026-08-13）。

用法:
  HF_HUB_OFFLINE=1 python tools/eval_reranker.py \
      --data 训练数据/reranker_training/baiweixi_reranker_train_v1.jsonl \
      --adapter 训练数据/reranker_training/reranker_baiweixi
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXED_INSTRUCTION = (
    "Given a current intimate conversation and a past memory, judge whether the "
    "memory should be activated as background for Bai Weixi's natural reply. "
    "Reject memories that are merely topically related but unnecessary. "
    "Activation does not mean the memory must be explicitly mentioned."
)


def build_text(query: str, memory: str) -> str:
    return f"<|im_start|>user\n{FIXED_INSTRUCTION}\n\n对话：{query}\n记忆：{memory}<|im_end|>"


def activation_probability(model, tokenizer, query: str, memory: str, device) -> float:
    import torch

    text = build_text(query, memory)
    enc = tokenizer(text, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"])
    logits = out.logits[:, -1, :]
    yes_id = tokenizer("yes", add_special_tokens=False)["input_ids"][-1]
    no_id = tokenizer("no", add_special_tokens=False)["input_ids"][-1]
    act = logits[0, yes_id] - logits[0, no_id]
    return float(torch.sigmoid(act).cpu())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--base-model", default="Qwen/Qwen3-Reranker-0.6B")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    device = "cuda" if torch.cuda.is_available() else "cpu"

    rows = [json.loads(l) for l in Path(args.data).read_text(encoding="utf-8").splitlines() if l.strip()]
    # 分组防泄漏切分（与训练一致）
    groups = {}
    for r in rows:
        g = r.get("conversation_group_id", "")
        groups.setdefault(g, []).append(r)
    gkeys = sorted(groups)
    n_dev = max(1, len(gkeys) // 10)
    dev_rows = [r for g in gkeys[:n_dev] for r in groups[g]]

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model, trust_remote_code=True,
        torch_dtype=torch.bfloat16, device_map="auto",
    )
    model = PeftModel.from_pretrained(base, args.adapter)
    model.eval()

    # 1. dev 集准确率（阈值 0.5）
    correct = 0
    pos_total = neg_total = 0
    pos_correct = neg_correct = 0
    for r in dev_rows:
        try:
            q = json.loads(r["query_text"])
        except (json.JSONDecodeError, TypeError):
            q = {}
        query = q.get("current_user_message") or q.get("working_state") or ""
        memory = r["candidate_memory_text"]
        label = 1.0 if r["label"] == "positive" else 0.0
        p = activation_probability(model, tokenizer, query, memory, device)
        pred = 1.0 if p >= 0.5 else 0.0
        correct += (pred == label)
        if label == 1:
            pos_total += 1
            pos_correct += (pred == 1)
        else:
            neg_total += 1
            neg_correct += (pred == 0)
    print(f"dev 集: {len(dev_rows)} 条")
    print(f"  准确率: {correct}/{len(dev_rows)} = {correct/len(dev_rows)*100:.1f}%")
    print(f"  positive 召回: {pos_correct}/{pos_total} = {pos_correct/pos_total*100:.1f}%")
    print(f"  negative 拒绝: {neg_correct}/{neg_total} = {neg_correct/neg_total*100:.1f}%")

    # 2. 真实场景推理演示（同一 query 多候选打分排序）
    print("\n=== 真实场景推理演示 ===")
    demo_query = "腿又疼了吗？外面下着雨。"
    demo_candidates = [
        ("ev:bwx_accident", "[Day 0·暴雨夜] 在松江府过马路时被车辆擦碰，后腿和身体侧面受伤"),
        ("facts:cardboard_box", "最初救助时使用的纸箱仍放在出租屋内，是白未晞的安全地点"),
        ("ev:bwx_forest_childhood", "[幼年早期] 以普通纯白小猫的形态独自在深山老林中流浪"),
        ("facts:current_injury", "外伤恢复大半，日常活动基本正常，快速行动仍可能轻微疼痛"),
        ("ev:bwx_spirit_fruit", "[幼年] 因饥饿误食蕴含浓郁妖力的野果，妖力觉醒"),
    ]
    scored = []
    for mid, text in demo_candidates:
        p = activation_probability(model, tokenizer, demo_query, text, device)
        scored.append((p, mid, text))
    scored.sort(reverse=True)
    for p, mid, text in scored:
        print(f"  {p:.2f} | {mid} | {text[:40]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
