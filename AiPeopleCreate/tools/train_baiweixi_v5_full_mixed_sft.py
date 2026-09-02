"""Train Gemma 4 on the full Baiweixi corpus plus audited V5 grounded data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch
from datasets import Dataset
from trl import SFTConfig
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template
from unsloth.trainer import UnslothTrainer


CREATE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CREATE_ROOT.parent
AI_ROOT = REPO_ROOT / "AiPeople"
TRAINING_ROOT = AI_ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b"
MODEL_PATH = AI_ROOT / "training_packages" / "models" / "Gemma-4-12B-it"
V4_READY = AI_ROOT / "training_packages" / "training_package_baiweixi_qwen35_27b" / "data" / "baiweixi_27b_ready.jsonl"
V4_SOURCE = CREATE_ROOT / "训练数据" / "baiweixi_v4_training.jsonl"
V4_METADATA = CREATE_ROOT / "训练数据" / "baiweixi_v4_final.metadata.jsonl"
V5_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_v5_pilot_v1"
V5_TRAIN = V5_ROOT / "package" / "train.jsonl"
V5_FREEZE = V5_ROOT / "package_freeze_manifest_20260831.json"
SUPPLEMENT_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_v5_targeted_supplement_v1"
SUPPLEMENT = SUPPLEMENT_ROOT / "train_supplement.jsonl"
SUPPLEMENT_MANIFEST = SUPPLEMENT_ROOT / "manifest.json"
DEFAULT_OUTPUT = TRAINING_ROOT / "outputs" / "baiweixi_v5_full_mixed_adapter"
MAX_SEQ_LENGTH = 1280
SEED = 20260901
IGNORE_INDEX = -100
ROLE_MAP = {"system": "system", "human": "user", "gpt": "assistant"}
TARGET_NAMES = {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"}
V5_GROUNDED_TYPES = {
    "reply_accept_authoritative_update",
    "reply_reject_stale_claim",
    "reply_correct_false_premise",
    "reply_resolve_world_memory_conflict",
    "reply_confirm_current_state",
    "reply_subject_attribution",
    "reply_insufficient_information",
    "reply_direct_answer",
}

if str(TRAINING_ROOT) not in sys.path:
    sys.path.insert(0, str(TRAINING_ROOT))

from supervision import build_all_assistant_example


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def signature(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verify_sources() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    v4_rows = load_jsonl(V4_READY)
    v4_source = load_jsonl(V4_SOURCE)
    v4_metadata = load_jsonl(V4_METADATA)
    if not (len(v4_rows) == len(v4_source) == len(v4_metadata) == 1973):
        raise RuntimeError("V4 full corpus must contain 1973 aligned rows")
    for index, (ready, source, metadata) in enumerate(zip(v4_rows, v4_source, v4_metadata, strict=True)):
        if not metadata.get("task_type"):
            raise RuntimeError(f"V4 metadata has no task_type at row {index}")
        if len(ready.get("conversations") or []) != len(source.get("conversations") or []):
            raise RuntimeError(f"V4 ready/source turn count differs at row {index}")

    freeze = json.loads(V5_FREEZE.read_text(encoding="utf-8"))
    review = freeze.get("human_review") or {}
    if (review.get("total"), review.get("approved"), review.get("rejected")) != (80, 80, 0):
        raise RuntimeError("V5 frozen package is not bound to the 80/80 human approval")
    for relative, expected in freeze["package_files_sha256"].items():
        if sha256(V5_ROOT / relative) != expected:
            raise RuntimeError(f"V5 frozen file hash changed: {relative}")

    supplement_manifest = json.loads(SUPPLEMENT_MANIFEST.read_text(encoding="utf-8"))
    expected_supplement_hash = supplement_manifest["files"]["train_supplement.jsonl"]
    supplement_rows = load_jsonl(SUPPLEMENT)
    if len(supplement_rows) != 24 or sha256(SUPPLEMENT) != expected_supplement_hash:
        raise RuntimeError("V5 targeted supplement changed")
    return v4_rows, v4_metadata, supplement_rows


def v5_task_map(supplement_rows: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for source in load_jsonl(V5_ROOT / "inputs" / "grounded_candidates.jsonl"):
        scenario = source["scenario"]
        conversations = [
            {"from": "system", "value": "你是白未晞。只输出角色说出口的自然回复。"},
            {"from": "human", "value": json.dumps(scenario["model_view"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
            {"from": "gpt", "value": source["teacher_target"]["reply"].strip()},
        ]
        result[signature(conversations)] = str(scenario["task_type"])
    for source in load_jsonl(V5_ROOT / "inputs" / "static_records.jsonl"):
        result[signature(source["conversations"])] = str(source.get("task_type") or "reply_casual")
    for source in supplement_rows:
        key = signature(source["conversations"])
        if key in result:
            raise RuntimeError(f"supplement duplicates V5 package: {source.get('sample_id')}")
        result[key] = str(source["task_type"])
    return result


def collect_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    v4_rows, v4_metadata, supplement_rows = verify_sources()
    rows: list[dict[str, Any]] = []
    for row, metadata in zip(v4_rows, v4_metadata, strict=True):
        rows.append({"source": "v4_full", "task": f"v4.{metadata['task_type']}", "conversations": row["conversations"]})

    task_map = v5_task_map(supplement_rows)
    v5_grounded = []
    excluded_static = 0
    for row in load_jsonl(V5_TRAIN):
        task = task_map.get(signature(row["conversations"]))
        if task in V5_GROUNDED_TYPES:
            v5_grounded.append({"source": "v5_grounded", "task": f"v5.{task}", "conversations": row["conversations"]})
        elif task == "reply_casual":
            excluded_static += 1
        else:
            raise RuntimeError(f"unmapped V5 train row: {task}")
    if len(v5_grounded) != 192 or excluded_static != 126:
        raise RuntimeError(f"unexpected V5 train composition: grounded={len(v5_grounded)}, static={excluded_static}")
    rows.extend(v5_grounded)
    rows.extend(
        {"source": "v5_supplement", "task": f"v5.{row['task_type']}", "conversations": row["conversations"]}
        for row in supplement_rows
    )
    if len(rows) != 2189:
        raise RuntimeError(f"full mixed corpus must contain 2189 rows, got {len(rows)}")
    signatures = [signature(row["conversations"]) for row in rows]
    if len(signatures) != len(set(signatures)):
        raise RuntimeError("exact duplicate conversations remain in full mixed corpus")
    composition = {
        "total_rows": len(rows),
        "v4_full_rows": 1973,
        "v5_grounded_rows": len(v5_grounded),
        "v5_supplement_rows": len(supplement_rows),
        "v5_duplicate_static_rows_excluded": excluded_static,
        "dev_test_sealed_in_training": False,
    }
    return rows, composition


def build_dataset(tokenizer: Any, rows: list[dict[str, Any]]) -> tuple[Dataset, dict[str, Any]]:
    examples = []
    by_task: dict[str, Counter[str]] = {}
    max_tokens = 0
    total_assistant = 0
    total_supervised = 0
    for row_index, row in enumerate(rows):
        conversations = row["conversations"]
        messages = [{"role": ROLE_MAP[item["from"]], "content": str(item["value"])} for item in conversations]
        example, spans = build_all_assistant_example(tokenizer, messages, max_seq_length=MAX_SEQ_LENGTH, row_index=row_index)
        assistant_count = sum(message["role"] == "assistant" for message in messages)
        if assistant_count != len(spans) or not spans:
            raise RuntimeError(f"assistant supervision incomplete at row {row_index}")
        token_count = len(example["input_ids"])
        max_tokens = max(max_tokens, token_count)
        supervised_tokens = sum(span.token_count for span in spans)
        stats = by_task.setdefault(row["task"], Counter())
        stats["rows"] += 1
        stats["assistant_total"] += assistant_count
        stats["supervised_messages"] += len(spans)
        stats["supervised_tokens"] += supervised_tokens
        total_assistant += assistant_count
        total_supervised += supervised_tokens
        examples.append({"input_ids": example["input_ids"], "attention_mask": example["attention_mask"], "labels": example["labels"]})
    audit = {
        "rows": len(rows),
        "assistant_total": total_assistant,
        "supervised_messages": total_assistant,
        "supervised_tokens": total_supervised,
        "assistant_coverage_percent": 100.0,
        "max_sequence_tokens": max_tokens,
        "truncation_allowed": False,
        "by_task": {key: dict(value) for key, value in sorted(by_task.items())},
    }
    return Dataset.from_list(examples), audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    args = parser.parse_args()
    if not torch.cuda.is_available() or not MODEL_PATH.is_dir():
        raise RuntimeError("CUDA or frozen Gemma model is unavailable")
    output_dir = args.output_dir.resolve()
    if (output_dir / "adapter_model.safetensors").exists():
        raise RuntimeError(f"refusing to overwrite completed Adapter: {output_dir}")

    rows, composition = collect_rows()
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH), max_seq_length=MAX_SEQ_LENGTH, dtype=torch.bfloat16,
        load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True,
        use_gradient_checkpointing="unsloth", random_state=SEED, attn_implementation="eager",
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    targets = sorted(
        name for name, _ in model.named_modules()
        if "language_model.layers." in name and name.rsplit(".", 1)[-1] in TARGET_NAMES
    )
    if len(targets) != 328:
        raise RuntimeError(f"unexpected Gemma language target count: {len(targets)}")
    model = FastModel.get_peft_model(
        model, r=8, target_modules=targets, lora_alpha=16, lora_dropout=0.05,
        bias="none", use_gradient_checkpointing="unsloth", random_state=SEED,
    )
    dataset, audit = build_dataset(tokenizer, rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    write = lambda name, value: (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    write("dataset_manifest.json", {
        "schema_version": 1,
        "dataset_id": "baiweixi_v5_full_mixed_sft_v1",
        "composition": composition,
        "files": {
            "v4_ready_sha256": sha256(V4_READY),
            "v4_metadata_sha256": sha256(V4_METADATA),
            "v5_train_sha256": sha256(V5_TRAIN),
            "v5_freeze_sha256": sha256(V5_FREEZE),
            "v5_supplement_sha256": sha256(SUPPLEMENT),
        },
    })
    write("supervision_audit.json", audit)
    print(json.dumps({"composition": composition, "supervision_audit": audit}, ensure_ascii=False, indent=2), flush=True)

    training_args = SFTConfig(
        output_dir=str(output_dir), dataset_text_field="text", max_length=MAX_SEQ_LENGTH,
        packing=False, padding_free=False, per_device_train_batch_size=1,
        gradient_accumulation_steps=8, learning_rate=args.learning_rate,
        num_train_epochs=1.0, max_steps=-1, lr_scheduler_type="cosine", warmup_ratio=0.05,
        weight_decay=0.01, max_grad_norm=1.0, bf16=True, fp16=False,
        gradient_checkpointing=True, optim="adamw_bnb_8bit", seed=SEED, data_seed=SEED,
        logging_steps=5, save_strategy="steps", save_steps=50, save_total_limit=2,
        report_to="none", dataset_num_proc=1,
    )
    trainer = UnslothTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=training_args)
    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    result = trainer.train()
    elapsed = time.time() - started
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    trainer.save_state()
    adapter = output_dir / "adapter_model.safetensors"
    metrics = dict(result.metrics)
    metrics.update({
        "status": "completed",
        "elapsed_seconds": elapsed,
        "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2,
        "learning_rate": args.learning_rate,
        "num_train_epochs": 1.0,
        "combined_dataset_rows": len(rows),
        "adapter_sha256": sha256(adapter),
        "composition": composition,
        "supervision_audit": audit,
    })
    write("run_metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
