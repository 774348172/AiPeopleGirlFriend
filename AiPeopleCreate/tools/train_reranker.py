# -*- coding: utf-8 -*-
"""Qwen3 记忆选择器（reranker）训练脚本（2026-08-13，SELECT-04 验证）。

按《全局记忆选择器训练与模型决策方案_V2》§9 实现：
- 对每个 query-memory pair 计算 activation_logit = logit("yes") - logit("no")
- loss = BCEWithLogits(activation_logit, hard_label)
- 先 LoRA/QLoRA 验证数据和任务合同（不一次上全参微调）

基座：本地缓存的 Qwen3-1.7B（Qwen3-Reranker-0.6B 需联网下载，本机不可达；
方案 V2 明确 Qwen 模型承担精排职责，1.7B 为同族可验证底座）。

数据：训练数据/reranker_training/baiweixi_reranker_train_v1.jsonl
（640 条单对记录：query_text + candidate_memory_text + label + relevance_score）

用法:
  python tools/train_reranker.py --data 训练数据/reranker_training/baiweixi_reranker_train_v1.jsonl \
      --epochs 2 --out outputs/reranker_baiweixi
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


def load_pairs(data_path: Path) -> list[dict]:
    rows = [json.loads(l) for l in data_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    pairs = []
    for r in rows:
        # query_text 是 JSON 序列化的 query 对象 → 解析出 current_user_message 作为查询
        try:
            q = json.loads(r["query_text"])
        except (json.JSONDecodeError, TypeError):
            q = {}
        query = q.get("current_user_message") or q.get("working_state") or ""
        pairs.append({
            "query": query,
            "memory": r["candidate_memory_text"],
            "label": 1.0 if r["label"] == "positive" else 0.0,
            "score": float(r.get("relevance_score") or 0.0),
            "group": r.get("conversation_group_id", ""),
        })
    return pairs


def build_dataset(pairs: list[dict], tokenizer, max_len: int = 256):
    """编码 query + memory 对 → torch Dataset（input_ids/attention_mask/labels）。"""
    import torch
    from torch.utils.data import Dataset

    texts = [
        f"<|im_start|>user\n{FIXED_INSTRUCTION}\n\n对话：{p['query']}\n记忆：{p['memory']}<|im_end|>"
        for p in pairs
    ]
    enc = tokenizer(texts, padding="max_length", truncation=True,
                    max_length=max_len, return_tensors="pt")

    class PairDataset(Dataset):
        def __len__(self):
            return len(pairs)

        def __getitem__(self, idx):
            return {
                "input_ids": enc["input_ids"][idx],
                "attention_mask": enc["attention_mask"][idx],
                "labels": torch.tensor(pairs[idx]["label"], dtype=torch.float32),
            }

    return PairDataset()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", default="Qwen/Qwen3-1.7B")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--lora-rank", type=int, default=16)
    ap.add_argument("--out", default="outputs/reranker_baiweixi")
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--quant-4bit", action="store_true", help="8G 显存用 4bit QLoRA")
    args = ap.parse_args()

    import torch
    from transformers import (
        AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, Trainer, TrainingArguments,
    )
    from peft import LoraConfig, get_peft_model

    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    pairs = load_pairs(Path(args.data))
    print(f"加载数据: {len(pairs)} 条 pair（positive {sum(1 for p in pairs if p['label']==1)} / negative {sum(1 for p in pairs if p['label']==0)}）")

    # 分组防泄漏：按 conversation_group_id 切分
    groups = {}
    for p in pairs:
        groups.setdefault(p["group"], []).append(p)
    gkeys = sorted(groups)
    n_dev = max(1, len(gkeys) // 10)
    dev_groups, train_groups = set(gkeys[:n_dev]), set(gkeys[n_dev:])
    train_pairs = [p for g in train_groups for p in groups[g]]
    dev_pairs = [p for g in dev_groups for p in groups[g]]
    print(f"切分: train {len(train_pairs)} / dev {len(dev_pairs)}（按对话组防泄漏）")

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model_kwargs = {"trust_remote_code": True}
    if args.quant_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
    model = AutoModelForCausalLM.from_pretrained(args.model, **model_kwargs)
    model.config.pad_token_id = tokenizer.pad_token_id

    # LoRA
    lora = LoraConfig(
        r=args.lora_rank, lora_alpha=args.lora_rank * 2, lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    class RerankerTrainer(Trainer):
        """按 yes/no logits 计算 BCEWithLogits loss（方案 V2 §9.3）。"""

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            labels = inputs.pop("labels")
            out = model(
                input_ids=inputs["input_ids"],
                attention_mask=inputs["attention_mask"],
                return_dict=True,
            )
            logits = out.logits[:, -1, :]  # 末 token logits
            vocab = tokenizer("yes", add_special_tokens=False)["input_ids"][-1]
            vocab_no = tokenizer("no", add_special_tokens=False)["input_ids"][-1]
            activation_logit = logits[:, vocab] - logits[:, vocab_no]
            loss = torch.nn.functional.binary_cross_entropy_with_logits(
                activation_logit, labels)
            return (loss, {"activation_logit": activation_logit}) if return_outputs else loss

    train_ds = build_dataset(train_pairs, tokenizer, args.max_len)
    dev_ds = build_dataset(dev_pairs, tokenizer, args.max_len)

    training_args = TrainingArguments(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        weight_decay=0.01,
        bf16=True,
        gradient_checkpointing=True,
        logging_steps=20,
        save_strategy="epoch",
        eval_strategy="epoch",
        save_total_limit=2,
        remove_unused_columns=False,
    )
    trainer = RerankerTrainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=dev_ds,
    )
    trainer.train()
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"训练完成 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
