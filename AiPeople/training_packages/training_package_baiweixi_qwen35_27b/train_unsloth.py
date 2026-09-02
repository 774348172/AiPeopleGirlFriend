from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch
from datasets import load_dataset
from unsloth import FastModel
from unsloth.chat_templates import train_on_responses_only
from unsloth.trainer import UnslothTrainer
from trl import SFTConfig


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT.parent / "models" / "Qwen3.5-27B"
DATA_PATH = ROOT / "data" / "baiweixi_27b_ready.jsonl"
DEFAULT_OUTPUT = ROOT / "outputs" / "baiweixi_27b_unsloth"
SEED = 20260825
ROLE_MAP = {"system": "system", "human": "user", "gpt": "assistant"}
TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
    "in_proj_qkv",
    "in_proj_z",
    "in_proj_b",
    "in_proj_a",
    "out_proj",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qwen3.5-27B Baiweixi QLoRA training")
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume-from-checkpoint", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    is_probe = args.max_steps > 0
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this Python environment")

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=1024,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )
    model = FastModel.get_peft_model(
        model,
        r=8,
        target_modules=TARGET_MODULES,
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )

    def format_row(row: dict) -> dict:
        messages = [
            {"role": ROLE_MAP[item["from"]], "content": item["value"]}
            for item in row["conversations"]
        ]
        return {
            "text": tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=False,
                enable_thinking=False,
            )
        }

    dataset = load_dataset("json", data_files=str(DATA_PATH), split="train")
    dataset = dataset.map(
        format_row,
        remove_columns=dataset.column_names,
        num_proc=1,
        desc="Formatting Baiweixi conversations",
    )
    if is_probe:
        dataset = dataset.select(range(min(args.max_steps, len(dataset))))

    training_args = SFTConfig(
        output_dir=str(args.output_dir),
        dataset_text_field="text",
        max_length=1024,
        packing=False,
        padding_free=False,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=1 if is_probe else 8,
        learning_rate=5e-5,
        num_train_epochs=1.0,
        max_steps=args.max_steps,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        weight_decay=0.01,
        max_grad_norm=1.0,
        bf16=True,
        fp16=False,
        gradient_checkpointing=True,
        optim="adamw_bnb_8bit",
        seed=SEED,
        data_seed=SEED,
        logging_steps=1 if is_probe else 5,
        save_strategy="no" if is_probe else "steps",
        save_steps=100,
        save_total_limit=2,
        report_to="none",
        dataset_num_proc=1,
    )
    trainer = UnslothTrainer(
        model=model,
        processing_class=tokenizer,
        train_dataset=dataset,
        args=training_args,
    )
    trainer = train_on_responses_only(trainer, tokenizer=tokenizer, num_proc=1)

    trainable = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    total = sum(parameter.numel() for parameter in model.parameters())
    print(f"Trainable parameters: {trainable:,} / {total:,} ({100 * trainable / total:.4f}%)")

    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    result = trainer.train(resume_from_checkpoint=args.resume_from_checkpoint)
    elapsed = time.time() - started
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    trainer.save_state()

    metrics = dict(result.metrics)
    metrics.update(
        {
            "elapsed_seconds_measured": elapsed,
            "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "trainable_parameters": trainable,
            "total_parameters_loaded": total,
            "base_model": str(MODEL_PATH),
            "dataset": str(DATA_PATH),
            "max_steps_argument": args.max_steps,
            "seed": SEED,
        }
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "run_metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(metrics, handle, ensure_ascii=False, indent=2)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
