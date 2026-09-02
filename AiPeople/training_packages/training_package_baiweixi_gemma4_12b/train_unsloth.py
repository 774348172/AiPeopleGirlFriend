from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import torch
from datasets import Dataset, load_dataset
from unsloth.trainer import UnslothTrainer
from trl import SFTConfig

from supervision import IGNORE_INDEX, build_all_assistant_example


ROOT = Path(__file__).resolve().parent
PACKAGES_ROOT = ROOT.parent
MODEL_PATH = PACKAGES_ROOT / "models" / "Gemma-4-12B-it"
DATA_PATH = (
    PACKAGES_ROOT
    / "training_package_baiweixi_qwen35_27b"
    / "data"
    / "baiweixi_27b_ready.jsonl"
)
SOURCE_DATA_PATH = PACKAGES_ROOT.parent.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_v4_training.jsonl"
METADATA_PATH = PACKAGES_ROOT.parent.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_v4_final.metadata.jsonl"
DEFAULT_OUTPUT = ROOT / "outputs" / "baiweixi_gemma4_12b_all_assistant_v2"
SEED = 20260826
MAX_SEQ_LENGTH = 1280
ROLE_MAP = {"system": "system", "human": "user", "gpt": "assistant"}
TARGET_MODULE_NAMES = {
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
}
EXPECTED_LANGUAGE_TARGET_COUNT = 328
CORE_BEHAVIOR_TYPES = {
    "reply_boundary",
    "reply_canon_qa",
    "reply_casual",
    "reply_correction",
    "reply_emotion",
    "reply_general",
    "reply_identity",
    "reply_item",
    "reply_memory",
    "reply_protective",
    "reply_quiet_company",
    "reply_romance",
    "reply_safety",
    "reply_supportive",
    "reply_vague",
}
ACTION_MARKUP = re.compile(r"（[^）]*）|\([^)]{2,}\)|\*[^*]+\*")
PURE_DIALOGUE_RULE = (
    "\n最终回复只输出玩家能够直接听见的对白纯文本，不输出动作、表情、神态、"
    "姿态、视线、心理或环境旁白，也不用括号、星号或标签包装舞台说明。"
    "先直接回应玩家当前这句话；没有必要时不要补充无依据的行动、关心或新事实。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gemma 4 12B Baiweixi QLoRA training")
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume-from-checkpoint", default=None)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def prepare_expected_conversations(source_conversations: list[dict]) -> list[dict]:
    expected: list[dict[str, str]] = []
    for message in source_conversations:
        role = message["from"]
        value = message["value"]
        if role == "system" and PURE_DIALOGUE_RULE.strip() not in value:
            value = value.rstrip() + PURE_DIALOGUE_RULE
        elif role == "gpt":
            value = ACTION_MARKUP.sub("", value)
            value = re.sub(r"[ \t]+\n", "\n", value)
            value = re.sub(r"\n{3,}", "\n\n", value).strip()
        expected.append({"from": role, "value": value})
    return expected


def validate_metadata_alignment(raw_dataset, source_rows: list[dict], metadata_rows: list[dict]) -> None:
    if len(raw_dataset) != len(source_rows) or len(raw_dataset) != len(metadata_rows):
        raise RuntimeError(
            "Training data, source data, and metadata row counts differ: "
            f"{len(raw_dataset)}, {len(source_rows)}, {len(metadata_rows)}"
        )
    for row_index, (ready_row, source_row, metadata) in enumerate(
        zip(raw_dataset, source_rows, metadata_rows, strict=True)
    ):
        ready_conversations = ready_row["conversations"]
        expected_conversations = prepare_expected_conversations(source_row["conversations"])
        if ready_conversations != expected_conversations:
            raise RuntimeError(
                f"Metadata alignment failed at row {row_index}: packaged conversation is not "
                "the documented pure-dialogue transformation of its metadata source row"
            )
        if not metadata.get("task_type"):
            raise RuntimeError(f"Metadata row {row_index} has no task_type")


def build_supervised_dataset(tokenizer, raw_dataset, metadata_rows: list[dict]) -> tuple[Dataset, dict]:
    examples: list[dict] = []
    behavior_stats: dict[str, Counter] = {}
    total_assistant_messages = 0
    total_supervised_tokens = 0
    total_supervised_special_tokens = 0
    total_masked_special_tokens = 0
    special_ids = set(tokenizer.all_special_ids)

    for row_index, (row, metadata) in enumerate(zip(raw_dataset, metadata_rows, strict=True)):
        messages = [
            {"role": ROLE_MAP[item["from"]], "content": item["value"]}
            for item in row["conversations"]
        ]
        example, spans = build_all_assistant_example(
            tokenizer,
            messages,
            max_seq_length=MAX_SEQ_LENGTH,
            row_index=row_index,
        )
        behavior = str(metadata["task_type"])
        stats = behavior_stats.setdefault(behavior, Counter())
        assistant_total = sum(message["role"] == "assistant" for message in messages)
        stats["rows"] += 1
        stats["assistant_messages_total"] += assistant_total
        stats["assistant_spans_supervised"] += len(spans)
        stats["supervised_tokens"] += sum(span.token_count for span in spans)
        stats["supervised_special_tokens"] += sum(span.special_token_count for span in spans)
        total_assistant_messages += assistant_total
        total_supervised_tokens += sum(span.token_count for span in spans)
        total_supervised_special_tokens += sum(span.special_token_count for span in spans)
        total_masked_special_tokens += sum(
            token_id in special_ids and label == IGNORE_INDEX
            for token_id, label in zip(example["input_ids"], example["labels"], strict=True)
        )
        examples.append({**example, "source_row": row_index})

    observed_types = set(behavior_stats)
    if observed_types != CORE_BEHAVIOR_TYPES:
        raise RuntimeError(
            "Core behavior type set changed; update the explicit training contract. "
            f"Missing={sorted(CORE_BEHAVIOR_TYPES - observed_types)}, "
            f"unexpected={sorted(observed_types - CORE_BEHAVIOR_TYPES)}"
        )
    for behavior, stats in sorted(behavior_stats.items()):
        if stats["assistant_messages_total"] != stats["assistant_spans_supervised"]:
            raise RuntimeError(
                f"Core behavior {behavior} has masked assistant messages: "
                f"{stats['assistant_spans_supervised']}/{stats['assistant_messages_total']} supervised"
            )
        if stats["supervised_tokens"] <= 0:
            raise RuntimeError(f"Core behavior {behavior} has zero supervised tokens")

    if total_assistant_messages != sum(
        stats["assistant_spans_supervised"] for stats in behavior_stats.values()
    ):
        raise RuntimeError("Global assistant supervision coverage is incomplete")

    audit = {
        "source_rows": len(raw_dataset),
        "assistant_messages_total": total_assistant_messages,
        "assistant_spans_supervised": total_assistant_messages,
        "assistant_coverage_percent": 100.0,
        "supervised_tokens": total_supervised_tokens,
        "supervised_special_tokens": total_supervised_special_tokens,
        "masked_context_special_tokens": total_masked_special_tokens,
        "core_behavior_types": sorted(CORE_BEHAVIOR_TYPES),
        "behavior_types": {
            behavior: dict(sorted(stats.items()))
            for behavior, stats in sorted(behavior_stats.items())
        },
        "contract": {
            "all_assistant_spans_supervised": True,
            "non_assistant_context_masked": True,
            "text_prefix_boundaries_verified": True,
            "token_prefix_boundaries_verified": True,
            "turn_closing_special_tokens_supervised": True,
            "truncation_allowed": False,
        },
    }
    return Dataset.from_list(examples), audit


def main() -> None:
    args = parse_args()
    is_probe = args.max_steps > 0
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this Python environment")
    if not MODEL_PATH.is_dir():
        raise FileNotFoundError(f"Model directory is missing: {MODEL_PATH}")
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"Training dataset is missing: {DATA_PATH}")
    if not SOURCE_DATA_PATH.is_file():
        raise FileNotFoundError(f"Source dataset is missing: {SOURCE_DATA_PATH}")
    if not METADATA_PATH.is_file():
        raise FileNotFoundError(f"Training metadata is missing: {METADATA_PATH}")

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    language_target_modules = sorted(
        name
        for name, _module in model.named_modules()
        if "language_model.layers." in name
        and name.rsplit(".", 1)[-1] in TARGET_MODULE_NAMES
    )
    if len(language_target_modules) != EXPECTED_LANGUAGE_TARGET_COUNT:
        raise RuntimeError(
            "Unexpected Gemma 4 language LoRA target count: "
            f"{len(language_target_modules)} != {EXPECTED_LANGUAGE_TARGET_COUNT}"
        )
    model = FastModel.get_peft_model(
        model,
        r=8,
        target_modules=language_target_modules,
        lora_alpha=16,
        lora_dropout=0.05,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )
    unexpected_trainables = [
        name
        for name, parameter in model.named_parameters()
        if parameter.requires_grad and "language_model.layers." not in name
    ]
    if unexpected_trainables:
        raise RuntimeError(
            "LoRA unexpectedly enabled non-language parameters: "
            + ", ".join(unexpected_trainables[:5])
        )

    dataset = load_dataset("json", data_files=str(DATA_PATH), split="train")
    source_rows = load_jsonl(SOURCE_DATA_PATH)
    metadata_rows = load_jsonl(METADATA_PATH)
    validate_metadata_alignment(dataset, source_rows, metadata_rows)
    dataset, supervision_audit = build_supervised_dataset(tokenizer, dataset, metadata_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "supervision_audit.json").write_text(
        json.dumps(supervision_audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("All-assistant supervision audit passed:")
    print(json.dumps(supervision_audit, ensure_ascii=False, indent=2))
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
        save_steps=25,
        save_total_limit=12,
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
            "source_dataset": str(SOURCE_DATA_PATH.resolve()),
            "source_dataset_sha256": sha256(SOURCE_DATA_PATH),
            "metadata": str(METADATA_PATH.resolve()),
            "metadata_sha256": sha256(METADATA_PATH),
            "adapter_sha256": sha256(adapter_path),
            "max_steps_argument": args.max_steps,
            "seed": SEED,
            "target_module_names": sorted(TARGET_MODULE_NAMES),
            "language_target_module_count": len(language_target_modules),
            "training_quantization": "bitsandbytes NF4",
            "max_seq_length": MAX_SEQ_LENGTH,
            "chat_template": "gemma-4 normal mode",
            "loss_mask": "all assistant spans supervised; system and user context masked",
            "supervision_audit": supervision_audit,
        }
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "run_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
