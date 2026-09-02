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
from datasets import Dataset, load_dataset
from unsloth import FastLanguageModel
from unsloth.trainer import UnslothTrainer
from trl import SFTConfig


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT.parent / "models" / "Qwen2.5-14B-Instruct"
DATA_PATH = (
    ROOT.parent
    / "training_package_baiweixi_qwen35_27b"
    / "data"
    / "baiweixi_27b_ready.jsonl"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "baiweixi_qwen25_14b_unsloth"
MAX_SEQ_LENGTH = 1152
SEED = 20260828
ROLE_MAP = {"system": "system", "human": "user", "gpt": "assistant"}
TARGET_MODULES = [
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qwen2.5-14B Baiweixi QLoRA training")
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume-from-checkpoint", default=None)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render(tokenizer, messages: list[dict[str, str]], generation_prompt: bool) -> str:
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=generation_prompt,
    )


def build_supervised_examples(tokenizer, raw_dataset) -> tuple[Dataset, dict[str, int]]:
    examples: list[dict] = []
    assistant_messages = 0
    supervised_tokens = 0

    for row_index, row in enumerate(raw_dataset):
        messages = [
            {"role": ROLE_MAP[item["from"]], "content": item["value"]}
            for item in row["conversations"]
        ]
        full_text = render(tokenizer, messages, False)
        full = tokenizer(full_text, add_special_tokens=False, truncation=False)
        input_ids = full["input_ids"]
        if len(input_ids) > MAX_SEQ_LENGTH:
            raise ValueError(
                f"Row {row_index} has {len(input_ids)} tokens, above {MAX_SEQ_LENGTH}"
            )
        labels = [-100] * len(input_ids)

        assistant_ordinal = 0
        for message_index, message in enumerate(messages):
            if message["role"] != "assistant":
                continue
            assistant_messages += 1
            assistant_ordinal += 1

            prefix_text = render(tokenizer, messages[:message_index], True)
            through_text = render(tokenizer, messages[: message_index + 1], False)
            if not full_text.startswith(prefix_text) or not full_text.startswith(through_text):
                raise ValueError(
                    f"Chat template prefix mismatch at row {row_index}, assistant {assistant_ordinal}"
                )
            prefix_ids = tokenizer(
                prefix_text, add_special_tokens=False, truncation=False
            )["input_ids"]
            through_ids = tokenizer(
                through_text, add_special_tokens=False, truncation=False
            )["input_ids"]
            if input_ids[: len(prefix_ids)] != prefix_ids:
                raise ValueError(
                    f"Token prefix mismatch at row {row_index}, assistant {assistant_ordinal}"
                )
            if input_ids[: len(through_ids)] != through_ids:
                raise ValueError(
                    f"Token end mismatch at row {row_index}, assistant {assistant_ordinal}"
                )
            start, end = len(prefix_ids), len(through_ids)
            if start >= end:
                raise ValueError(
                    f"Assistant target has no tokens at row {row_index}, assistant {assistant_ordinal}"
                )
            labels[start:end] = input_ids[start:end]
            target_tokens = end - start
            supervised_tokens += target_tokens

        if assistant_ordinal == 0 or not any(token != -100 for token in labels):
            raise RuntimeError(f"Row {row_index} has no supervised assistant tokens")
        examples.append(
            {
                "input_ids": input_ids,
                "attention_mask": full["attention_mask"],
                "labels": labels,
                "source_row": row_index,
            }
        )

    if len(examples) != len(raw_dataset) or assistant_messages == 0:
        raise RuntimeError("Assistant supervision coverage is incomplete")
    if any(not any(token != -100 for token in example["labels"]) for example in examples):
        raise RuntimeError("At least one assistant example has zero supervised tokens")

    stats = {
        "source_rows": len(raw_dataset),
        "assistant_messages_found": assistant_messages,
        "assistant_spans_supervised": assistant_messages,
        "training_examples_created": len(examples),
        "assistant_coverage_percent": 100,
        "supervised_tokens": supervised_tokens,
        "dropped_context_messages": 0,
    }
    return Dataset.from_list(examples), stats


def main() -> None:
    args = parse_args()
    is_probe = args.max_steps > 0
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this Python environment")
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Base model is missing: {MODEL_PATH}")
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Training data is missing: {DATA_PATH}")

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=8,
        target_modules=TARGET_MODULES,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )

    raw_dataset = load_dataset("json", data_files=str(DATA_PATH), split="train")
    dataset, coverage = build_supervised_examples(tokenizer, raw_dataset)
    print("Loss-mask coverage check passed:")
    print(json.dumps(coverage, ensure_ascii=False, indent=2))

    if is_probe:
        dataset = dataset.select(range(min(args.max_steps, len(dataset))))
    dataset = dataset.remove_columns(["source_row"])

    training_args = SFTConfig(
        output_dir=str(args.output_dir),
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
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    trainer.save_state()

    adapter_path = args.output_dir / "adapter_model.safetensors"
    metrics = dict(result.metrics)
    metrics.update(
        {
            "elapsed_seconds_measured": elapsed,
            "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "trainable_parameters": trainable,
            "total_parameters_loaded": total,
            "base_model": str(MODEL_PATH.resolve()),
            "dataset": str(DATA_PATH.resolve()),
            "dataset_sha256": sha256(DATA_PATH),
            "adapter_sha256": sha256(adapter_path),
            "max_steps_argument": args.max_steps,
            "seed": SEED,
            "target_modules": TARGET_MODULES,
            "training_quantization": "bitsandbytes NF4",
            "max_seq_length": MAX_SEQ_LENGTH,
            "loss_mask": "all assistant spans supervised in each complete conversation; non-assistant context masked",
            **coverage,
        }
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "run_metrics.json").open("w", encoding="utf-8") as handle:
        json.dump(metrics, handle, ensure_ascii=False, indent=2)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
