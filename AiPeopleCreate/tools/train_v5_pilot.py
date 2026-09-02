"""Train one controlled V5 pilot Adapter and fail closed on data-contract drift."""
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

from datasets import Dataset
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template
from unsloth.trainer import UnslothTrainer
import torch
from trl import SFTConfig

CREATE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = CREATE_ROOT / "训练数据" / "baiweixi_v5_pilot_v1"
PACKAGE_DIR = PACKAGE / "package"
MODEL_PATH = CREATE_ROOT.parent / "AiPeople" / "training_packages" / "models" / "Gemma-4-12B-it"
DEFAULT_OUTPUT = CREATE_ROOT.parent / "AiPeople" / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs" / "baiweixi_v5_pilot_adapter"
DEFAULT_SUPPLEMENT = CREATE_ROOT / "训练数据" / "baiweixi_v5_targeted_supplement_v1"
FREEZE = PACKAGE / "package_freeze_manifest_20260831.json"
SYSTEM_ANCHOR = "你是白未晞。只输出角色说出口的自然回复。"
MAX_SEQ_LENGTH = 1280
ATTN_IMPLEMENTATION = "eager"
SEED = 20260831
IGNORE_INDEX = -100
CORE_TYPES = {
    "reply_accept_authoritative_update", "reply_reject_stale_claim",
    "reply_correct_false_premise", "reply_resolve_world_memory_conflict",
    "reply_confirm_current_state", "reply_subject_attribution",
    "reply_insufficient_information", "reply_direct_answer", "reply_casual",
}


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


def verify_freeze() -> dict[str, Any]:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    review = freeze.get("human_review", {})
    if (review.get("total"), review.get("approved"), review.get("rejected")) != (80, 80, 0):
        raise RuntimeError("V5 人工审核未达到 80/80 approved")
    for relative, expected in freeze["package_files_sha256"].items():
        path = PACKAGE / relative
        if sha256(path) != expected:
            raise RuntimeError(f"V5 freeze hash mismatch: {relative}")
    return freeze


def verify_supplement(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = path / "manifest.json"
    data_path = path / "train_supplement.jsonl"
    if not manifest_path.is_file() or not data_path.is_file():
        raise RuntimeError(f"targeted supplement is incomplete: {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset_id") != "baiweixi_v5_targeted_supplement_v1":
        raise RuntimeError("unexpected targeted supplement dataset_id")
    expected_counts = {
        "reply_insufficient_information": 8,
        "reply_subject_attribution": 8,
        "reply_accept_authoritative_update": 8,
    }
    if manifest.get("rows") != 24 or manifest.get("task_counts") != expected_counts:
        raise RuntimeError("targeted supplement count or task distribution changed")
    if manifest.get("rg0_rg10_all_passed") is not True:
        raise RuntimeError("targeted supplement did not pass RG0-RG10")
    if manifest.get("sealed_or_diagnostic_family_reuse") is not False:
        raise RuntimeError("targeted supplement reuses a sealed or diagnostic family")
    expected_hash = (manifest.get("files") or {}).get("train_supplement.jsonl")
    if not expected_hash or sha256(data_path) != expected_hash:
        raise RuntimeError("targeted supplement hash mismatch")
    rows = load_jsonl(data_path)
    if len(rows) != 24 or Counter(str(row.get("task_type")) for row in rows) != Counter(expected_counts):
        raise RuntimeError("targeted supplement rows do not match manifest")
    return rows, manifest


def audited_task_map(supplement_rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Map exported rows to task labels from the audited source, never by guessing text."""
    mapping: dict[str, str] = {}
    for row in load_jsonl(PACKAGE / "inputs" / "grounded_candidates.jsonl"):
        scenario, teacher = row["scenario"], row["teacher_target"]
        conversations = [
            {"from": "system", "value": SYSTEM_ANCHOR},
            {"from": "human", "value": json.dumps(scenario["model_view"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
            {"from": "gpt", "value": teacher["reply"].strip()},
        ]
        key = signature(conversations)
        task = str(scenario["task_type"])
        if key in mapping and mapping[key] != task:
            raise RuntimeError(f"V5 source signature has conflicting tasks: {task}")
        mapping[key] = task
    for row in load_jsonl(PACKAGE / "inputs" / "static_records.jsonl"):
        key = signature(row["conversations"])
        task = str(row.get("task_type") or "reply_casual")
        if key in mapping and mapping[key] != task:
            raise RuntimeError(f"static source signature has conflicting tasks: {task}")
        mapping[key] = task
    for row in supplement_rows or []:
        key = signature(row["conversations"])
        task = str(row.get("task_type"))
        if task not in CORE_TYPES:
            raise RuntimeError(f"supplement source has invalid task: {task}")
        if key in mapping:
            raise RuntimeError(f"supplement duplicates an existing audited conversation: {row.get('sample_id')}")
        mapping[key] = task
    return mapping


def build_dataset(tokenizer: Any, rows: list[dict[str, Any]], task_map: dict[str, str]) -> tuple[Dataset, dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    stats: dict[str, Counter[str]] = {}
    total_assistant = 0
    total_tokens = 0
    special_ids = set(getattr(tokenizer, "all_special_ids", []))
    role_map = {"system": "system", "human": "user", "gpt": "assistant"}
    for row_index, row in enumerate(rows):
        conversations = row.get("conversations")
        if not isinstance(conversations, list) or not conversations or conversations[0].get("from") != "system":
            raise RuntimeError(f"invalid V5 conversation at row {row_index}")
        task = task_map.get(signature(conversations))
        if task not in CORE_TYPES:
            raise RuntimeError(f"row {row_index} is not mapped to an audited core task: {task}")
        messages = [{"role": role_map[item["from"]], "content": str(item["value"])} for item in conversations]
        if messages[-1]["role"] != "assistant":
            raise RuntimeError(f"row {row_index} does not end with assistant")
        full_text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
        full = tokenizer(full_text, add_special_tokens=False, truncation=False)
        input_ids = list(full["input_ids"])
        if len(input_ids) > MAX_SEQ_LENGTH:
            raise RuntimeError(f"row {row_index} exceeds {MAX_SEQ_LENGTH} tokens")
        labels = [IGNORE_INDEX] * len(input_ids)
        assistant_indexes = [i for i, message in enumerate(messages) if message["role"] == "assistant"]
        supervised_messages = 0
        supervised_tokens = 0
        for message_index in assistant_indexes:
            prefix_text = tokenizer.apply_chat_template(messages[:message_index], tokenize=False, add_generation_prompt=True)
            through_text = tokenizer.apply_chat_template(messages[: message_index + 1], tokenize=False, add_generation_prompt=False)
            prefix_ids = list(tokenizer(prefix_text, add_special_tokens=False, truncation=False)["input_ids"])
            through_ids = list(tokenizer(through_text, add_special_tokens=False, truncation=False)["input_ids"])
            if input_ids[:len(prefix_ids)] != prefix_ids or input_ids[:len(through_ids)] != through_ids:
                raise RuntimeError(f"token boundary mismatch at row {row_index}, assistant {message_index}")
            start, end = len(prefix_ids), len(through_ids)
            if start >= end or (special_ids and not special_ids.intersection(input_ids[start:end])):
                raise RuntimeError(f"empty or unterminated assistant span at row {row_index}")
            content_ids = list(tokenizer(messages[message_index]["content"], add_special_tokens=False, truncation=False)["input_ids"])
            if not content_ids or not any(input_ids[o:o + len(content_ids)] == content_ids for o in range(len(input_ids) - len(content_ids) + 1)):
                raise RuntimeError(f"assistant content not found in target at row {row_index}")
            labels[start:end] = input_ids[start:end]
            supervised_messages += 1
            supervised_tokens += end - start
        if supervised_messages != len(assistant_indexes) or supervised_messages == 0:
            raise RuntimeError(f"assistant supervision mismatch at row {row_index}")
        item = stats.setdefault(task, Counter())
        item["rows"] += 1
        item["assistant_total"] += len(assistant_indexes)
        item["supervised_messages"] += supervised_messages
        item["supervised_tokens"] += supervised_tokens
        total_assistant += len(assistant_indexes)
        total_tokens += supervised_tokens
        examples.append({"input_ids": input_ids, "attention_mask": list(full["attention_mask"]), "labels": labels})
    if set(stats) != CORE_TYPES:
        raise RuntimeError(f"core behavior coverage mismatch: missing={sorted(CORE_TYPES - set(stats))}")
    for task, item in stats.items():
        if item["assistant_total"] != item["supervised_messages"] or item["supervised_tokens"] <= 0:
            raise RuntimeError(f"incomplete supervision for {task}: {dict(item)}")
    audit = {
        "rows": len(rows), "assistant_total": total_assistant,
        "supervised_messages": total_assistant, "supervised_tokens": total_tokens,
        "assistant_coverage_percent": 100.0,
        "by_task": {key: dict(value) for key, value in sorted(stats.items())},
        "all_assistant_spans_supervised": True, "non_assistant_context_masked": True,
    }
    return Dataset.from_list(examples), audit


def main() -> int:
    parser = argparse.ArgumentParser(description="V5 pilot Gemma 4 12B QLoRA")
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--supplement-dir", type=Path)
    args = parser.parse_args()
    freeze = verify_freeze()
    if not torch.cuda.is_available() or not MODEL_PATH.is_dir():
        raise RuntimeError("CUDA or frozen Gemma 4 model is unavailable")
    rows = load_jsonl(PACKAGE_DIR / "train.jsonl")
    if len(rows) != 318:
        raise RuntimeError(f"V5 train count changed: {len(rows)}")
    supplement_rows: list[dict[str, Any]] = []
    supplement_manifest: dict[str, Any] | None = None
    if args.supplement_dir is not None:
        supplement_rows, supplement_manifest = verify_supplement(args.supplement_dir)
        rows.extend(supplement_rows)
    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    model, tokenizer = FastModel.from_pretrained(model_name=str(MODEL_PATH), max_seq_length=MAX_SEQ_LENGTH, dtype=torch.bfloat16, load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True, use_gradient_checkpointing="unsloth", random_state=SEED, attn_implementation=ATTN_IMPLEMENTATION)
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    targets = sorted(name for name, _ in model.named_modules() if "language_model.layers." in name and name.rsplit(".", 1)[-1] in {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"})
    if len(targets) != 328:
        raise RuntimeError(f"unexpected Gemma language target count: {len(targets)}")
    model = FastModel.get_peft_model(model, r=8, target_modules=targets, lora_alpha=16, lora_dropout=0.05, bias="none", use_gradient_checkpointing="unsloth", random_state=SEED)
    dataset, audit = build_dataset(tokenizer, rows, audited_task_map(supplement_rows))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "v5_supervision_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"freeze_verified": True, "freeze": freeze["dataset_id"], "supervision_audit": audit}, ensure_ascii=False, indent=2), flush=True)
    if args.max_steps > 0:
        dataset = dataset.select(range(min(args.max_steps, len(dataset))))
    train_args = SFTConfig(output_dir=str(args.output_dir), dataset_text_field="text", max_length=MAX_SEQ_LENGTH, packing=False, padding_free=False, per_device_train_batch_size=1, gradient_accumulation_steps=1 if args.max_steps > 0 else 8, learning_rate=5e-5, num_train_epochs=1.0, max_steps=args.max_steps, lr_scheduler_type="cosine", warmup_ratio=0.05, weight_decay=0.01, max_grad_norm=1.0, bf16=True, fp16=False, gradient_checkpointing=True, optim="adamw_bnb_8bit", seed=SEED, data_seed=SEED, logging_steps=1 if args.max_steps > 0 else 5, save_strategy="no" if args.max_steps > 0 else "steps", save_steps=25, save_total_limit=12, report_to="none", dataset_num_proc=1)
    trainer = UnslothTrainer(model=model, processing_class=tokenizer, train_dataset=dataset, args=train_args)
    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    result = trainer.train()
    elapsed = time.time() - started
    trainer.save_model(str(args.output_dir))
    tokenizer.save_pretrained(str(args.output_dir))
    trainer.save_state()
    adapter = args.output_dir / "adapter_model.safetensors"
    metrics = dict(result.metrics)
    metrics.update({"status": "completed", "elapsed_seconds": elapsed, "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2, "base_model": str(MODEL_PATH.resolve()), "base_dataset": str(PACKAGE_DIR / "train.jsonl"), "base_dataset_rows": 318, "base_dataset_sha256": sha256(PACKAGE_DIR / "train.jsonl"), "supplement_dataset": str((args.supplement_dir / "train_supplement.jsonl").resolve()) if args.supplement_dir else None, "supplement_dataset_rows": len(supplement_rows), "supplement_dataset_sha256": sha256(args.supplement_dir / "train_supplement.jsonl") if args.supplement_dir else None, "supplement_manifest_sha256": sha256(args.supplement_dir / "manifest.json") if args.supplement_dir else None, "supplement_dataset_id": supplement_manifest.get("dataset_id") if supplement_manifest else None, "combined_dataset_rows": len(rows), "freeze_manifest_sha256": sha256(FREEZE), "adapter_sha256": sha256(adapter), "supervision_audit": audit, "training_quantization": "bitsandbytes NF4", "attention_implementation": ATTN_IMPLEMENTATION, "num_train_epochs": 1.0, "seed": SEED})
    (args.output_dir / "run_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
