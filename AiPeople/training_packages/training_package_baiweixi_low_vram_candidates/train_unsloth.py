from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch
from datasets import load_dataset
from unsloth import FastModel
from unsloth.trainer import UnslothTrainer
from trl import SFTConfig


ROOT = Path(__file__).resolve().parent
PACKAGES_ROOT = ROOT.parent
DATA_PATH = (
    PACKAGES_ROOT
    / "training_package_baiweixi_qwen35_27b"
    / "data"
    / "baiweixi_27b_ready.jsonl"
)
SEED = 20260825
MAX_SEQ_LENGTH = 1152
ROLE_MAP = {"system": "system", "human": "user", "gpt": "assistant"}
MODEL_CONFIGS = {
    "qwen35_9b": {
        "model_path": PACKAGES_ROOT / "models" / "Qwen3.5-9B",
        "output_name": "baiweixi_qwen35_9b_unsloth",
        "target_modules": [
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
        ],
    },
    "qwen3_14b": {
        "model_path": PACKAGES_ROOT / "models" / "Qwen3-14B",
        "output_name": "baiweixi_qwen3_14b_unsloth",
        "target_modules": [
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Baiweixi low-VRAM candidate QLoRA training")
    parser.add_argument("model_key", choices=sorted(MODEL_CONFIGS))
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume-from-checkpoint", default=None)
    return parser.parse_args()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    args = parse_args()
    config = MODEL_CONFIGS[args.model_key]
    model_path = Path(config["model_path"])
    output_dir = args.output_dir or ROOT / "outputs" / str(config["output_name"])
    is_probe = args.max_steps > 0
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this Python environment")
    if not model_path.is_dir():
        raise FileNotFoundError(f"model directory is missing: {model_path}")
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"training dataset is missing: {DATA_PATH}")

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(model_path),
        max_seq_length=MAX_SEQ_LENGTH,
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
        target_modules=list(config["target_modules"]),
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )

    def tokenize_row(row: dict) -> dict:
        messages = [
            {"role": ROLE_MAP[item["from"]], "content": item["value"]}
            for item in row["conversations"]
        ]
        if not messages or messages[-1]["role"] != "assistant":
            raise ValueError("Every training conversation must end with an assistant response")

        full_text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        assistant_prefix = tokenizer.apply_chat_template(
            messages[:-1],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        if not full_text.startswith(assistant_prefix):
            raise ValueError("Assistant prefix is not aligned with the full conversation")
        if not assistant_prefix.endswith("<think>\n\n</think>\n\n"):
            raise ValueError("Qwen non-thinking assistant prefix changed unexpectedly")

        encoded = tokenizer(
            full_text,
            add_special_tokens=False,
            truncation=True,
            max_length=MAX_SEQ_LENGTH,
        )
        prefix_ids = tokenizer(
            assistant_prefix,
            add_special_tokens=False,
            truncation=False,
        )["input_ids"]
        input_ids = encoded["input_ids"]
        if len(prefix_ids) >= len(input_ids):
            raise ValueError(
                "The final assistant response was truncated; increase MAX_SEQ_LENGTH"
            )

        # The role prompt and empty no-thinking envelope are context only. Masking
        # them prevents the role LoRA from learning to suppress the base model's
        # <think> entry token when inference explicitly enables thinking.
        labels = [-100] * len(prefix_ids) + input_ids[len(prefix_ids) :]
        return {
            "input_ids": input_ids,
            "attention_mask": encoded["attention_mask"],
            "labels": labels,
        }

    dataset = load_dataset("json", data_files=str(DATA_PATH), split="train")
    dataset = dataset.map(
        tokenize_row,
        remove_columns=dataset.column_names,
        num_proc=1,
        desc=f"Tokenizing Baiweixi conversations for {args.model_key}",
    )
    if is_probe:
        dataset = dataset.select(range(min(args.max_steps, len(dataset))))

    supervised_tokens = sum(
        sum(token_id != -100 for token_id in row["labels"]) for row in dataset
    )
    if supervised_tokens == 0:
        raise RuntimeError("The loss mask removed every assistant target token")
    for row in dataset:
        supervised_text = tokenizer.decode(
            [token_id for token_id in row["labels"] if token_id != -100],
            skip_special_tokens=False,
        )
        if "<think>" in supervised_text or "</think>" in supervised_text:
            raise RuntimeError("Thinking control tokens leaked into the supervised loss")
    print(
        f"Loss-mask check passed: {len(dataset):,} rows, "
        f"{supervised_tokens:,} supervised final-response tokens"
    )

    training_args = SFTConfig(
        output_dir=str(output_dir),
        dataset_text_field="text",
        max_length=MAX_SEQ_LENGTH,
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

    trainable = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    total = sum(parameter.numel() for parameter in model.parameters())
    print(f"Trainable parameters: {trainable:,} / {total:,} ({100 * trainable / total:.4f}%)")

    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    result = trainer.train(resume_from_checkpoint=args.resume_from_checkpoint)
    elapsed = time.time() - started
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    trainer.save_state()

    adapter_path = output_dir / "adapter_model.safetensors"
    metrics = dict(result.metrics)
    metrics.update(
        {
            "model_key": args.model_key,
            "elapsed_seconds_measured": elapsed,
            "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "trainable_parameters": trainable,
            "total_parameters_loaded": total,
            "base_model": str(model_path.resolve()),
            "dataset": str(DATA_PATH.resolve()),
            "dataset_sha256": sha256(DATA_PATH),
            "adapter_sha256": sha256(adapter_path),
            "max_steps_argument": args.max_steps,
            "seed": SEED,
            "target_modules": list(config["target_modules"]),
            "training_quantization": "bitsandbytes NF4",
            "max_seq_length": MAX_SEQ_LENGTH,
            "loss_mask": "final assistant response only; role prompt and empty thinking envelope masked",
            "supervised_tokens": supervised_tokens,
        }
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "run_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
