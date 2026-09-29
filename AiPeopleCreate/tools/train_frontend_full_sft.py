"""Train a separate Gemma frontend candidate using audited final-turn masks."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

CREATE_ROOT = Path(__file__).resolve().parents[1]
if str(CREATE_ROOT) not in sys.path:
    sys.path.insert(0, str(CREATE_ROOT))


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_row(row):
    messages = row["messages"]
    if row.get("render_profile_id") != "GAME_REPLY_CONTEXT_V1":
        raise ValueError("Wrong training profile")
    if row["supervised_message_indexes"] != [len(messages) - 1]:
        raise ValueError("Only final assistant may be supervised")
    if messages[0]["role"] != "system" or messages[-1]["role"] != "assistant" or messages[-2]["role"] != "user":
        raise ValueError("Invalid conversation boundary")
    if not messages[-1]["content"].strip():
        raise ValueError("Empty target")
    if "[当前权威事实]" not in messages[0]["content"]:
        raise ValueError("Missing production context")


def final_example(tokenizer, row, max_length, index):
    from data_gen_v4.adapters.modes.training import TrainingRecordV4
    from data_gen_v4.runtime_grounded.renderer import build_current_assistant_example
    validate_row(row)
    messages = row["messages"]
    record = TrainingRecordV4(
        sample_id=row["sample_id"], mode="GAME_REPLY",
        render_profile_id=row["render_profile_id"], messages=messages[1:],
        supervised_message_indexes=[len(messages) - 2], supervised_token_count=0,
        protocol_snapshot_id="GAME_REPLY_CONTEXT_V1",
        system_anchor=messages[0]["content"],
    )
    return build_current_assistant_example(
        tokenizer, record, max_seq_length=max_length, row_index=index
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--grounded-repeat", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--resume-from-checkpoint", type=Path)
    args = parser.parse_args()
    if not 1 <= args.grounded_repeat <= 8:
        raise ValueError("Invalid repeat")
    if (args.output / "adapter_model.safetensors").exists():
        raise ValueError("Refusing to replace completed Adapter")
    if args.resume_from_checkpoint is not None:
        args.resume_from_checkpoint = args.resume_from_checkpoint.resolve()
        required = {
            "adapter_model.safetensors", "optimizer.pt", "scheduler.pt",
            "rng_state.pth", "trainer_state.json",
        }
        missing = sorted(name for name in required if not (args.resume_from_checkpoint / name).is_file())
        if missing:
            raise ValueError(f"Incomplete resume checkpoint: {missing}")
        trainer_state = json.loads(
            (args.resume_from_checkpoint / "trainer_state.json").read_text(encoding="utf-8")
        )
        if trainer_state.get("global_step", 0) <= 0:
            raise ValueError("Resume checkpoint has no completed training steps")
    args.output.mkdir(parents=True, exist_ok=True)

    def write(name, data):
        path = args.output / name
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    write("status.json", {"stage": "validating_package", "pid": os.getpid()})
    try:
        manifest = json.loads((args.package / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("status") != "immutable_training_package_authorized"
                or manifest.get("training_authorized") is not True
                or manifest.get("render_profile_id") != "GAME_REPLY_CONTEXT_V1"
                or manifest.get("supervision_policy") != "final_assistant_only"):
            raise ValueError("Unapproved package")
        for filename, expected in manifest["files"].items():
            if sha(args.package / filename) != expected:
                raise ValueError(f"Frozen file changed: {filename}")
        from unsloth import FastModel
        from unsloth.chat_templates import get_chat_template
        from unsloth.trainer import UnslothTrainer
        from transformers import AutoTokenizer, TrainerCallback
        from datasets import Dataset
        from trl import SFTConfig
        import torch

        training_root = Path(__file__).resolve().parents[2] / "AiPeople/training_packages/training_package_baiweixi_gemma4_12b"
        model_path = training_root.parent / "models/Gemma-4-12B-it"
        sys.path.insert(0, str(training_root))
        tokenizer = get_chat_template(AutoTokenizer.from_pretrained(str(model_path), local_files_only=True), chat_template="gemma-4")
        template_sha = hashlib.sha256(tokenizer.chat_template.encode()).hexdigest()
        if manifest.get("chat_template_sha256") != template_sha:
            raise ValueError("Training tokenizer chat template differs from authorized preflight")
        if manifest.get("max_seq_length") != args.max_length:
            raise ValueError("Training max length differs from authorized preflight")
        stats, split_data = {}, {}
        for split in ("train", "dev"):
            rows = [json.loads(line) for line in (args.package / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(rows) != manifest["counts"][split]:
                raise ValueError(f"{split} row count differs from authorized manifest")
            examples = []
            for index, row in enumerate(rows):
                example, span = final_example(tokenizer, row, args.max_length, index)
                repeat = args.grounded_repeat if split == "train" and row["source"] != "persona" else 1
                examples.extend([example] * repeat)
                group = stats.setdefault(split + ":" + row["source"] + ":" + row["task_type"], Counter())
                group.update(unique_rows=1, supervised_messages=1, supervised_tokens=span.token_count,
                             effective_rows=repeat, effective_supervised_tokens=span.token_count * repeat)
                group["max_sequence_tokens"] = max(group["max_sequence_tokens"], len(example["input_ids"]))
                if index % 250 == 0:
                    print(f"TOKENIZE {split} {index}/{len(rows)}", flush=True)
                    write("status.json", {"stage": "tokenizing", "split": split, "processed": index, "total": len(rows), "pid": os.getpid()})
            split_data[split] = Dataset.from_list(examples).shuffle(seed=20260908)
        fact_tokens = sum(s["effective_supervised_tokens"] for key, s in stats.items() if key.startswith("train:") and not key.startswith("train:persona:"))
        total_tokens = sum(s["effective_supervised_tokens"] for key, s in stats.items() if key.startswith("train:"))
        audit = {"by_task": stats, "grounded_repeat": args.grounded_repeat,
                 "effective_grounded_token_share": fact_tokens / total_tokens,
                 "effective_train_rows": len(split_data["train"]), "all_original_assistant_targets_retained_for_included_rows": True,
                 "history_labels_masked": True, "truncation": False}
        write("supervision_audit.json", audit)
        write("dataset_manifest.json", manifest)
        config = {"base_model": str(model_path), "dataset_sha256": sha(args.package / "train.jsonl"),
                  "chat_template_sha256": template_sha,
                  "rank": 8, "alpha": 16, "dropout": 0.05, "learning_rate": 2e-5, "epochs": 1,
                  "max_length": args.max_length, "grounded_repeat": args.grounded_repeat,
                  "effective_rows": len(split_data["train"]), "production_promoted": False}
        write("training_config.json", config)
        print("PREFLIGHT " + json.dumps({"rows": len(split_data["train"]), "grounded_token_share": fact_tokens / total_tokens}), flush=True)
        if args.preflight_only:
            write("status.json", {"stage": "preflight_passed", "training_started": False})
            return
        write("status.json", {"stage": "loading_model", "pid": os.getpid(),
            "resume_from_checkpoint": str(args.resume_from_checkpoint) if args.resume_from_checkpoint else None})
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable")
        torch.manual_seed(20260908)
        model, model_tokenizer = FastModel.from_pretrained(model_name=str(model_path), max_seq_length=args.max_length,
            dtype=torch.bfloat16, load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True,
            use_gradient_checkpointing="unsloth", random_state=20260908, attn_implementation="eager")
        model_tokenizer = get_chat_template(model_tokenizer, chat_template="gemma-4")
        if model_tokenizer.chat_template != tokenizer.chat_template or model_tokenizer.get_vocab() != tokenizer.get_vocab():
            raise ValueError("Preflight and loaded model tokenizers differ")
        targets = sorted(name for name, _ in model.named_modules() if "language_model.layers." in name and name.rsplit(".", 1)[-1] in {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"})
        if len(targets) != 328:
            raise RuntimeError(f"Unexpected language targets: {len(targets)}")
        model = FastModel.get_peft_model(model, r=8, target_modules=targets, lora_alpha=16, lora_dropout=0.05,
            bias="none", use_gradient_checkpointing="unsloth", random_state=20260908)

        class Progress(TrainerCallback):
            def on_log(self, args, state, control, logs=None, **kwargs):
                write("status.json", {"stage": "training", "step": state.global_step, "max_steps": state.max_steps,
                    "epoch": state.epoch, "metrics": logs, "pid": os.getpid()})

        settings = SFTConfig(output_dir=str(args.output), dataset_text_field="text", max_length=args.max_length,
            packing=False, padding_free=False, per_device_train_batch_size=1, gradient_accumulation_steps=8,
            learning_rate=2e-5, num_train_epochs=1.0, lr_scheduler_type="cosine", warmup_ratio=0.05,
            weight_decay=0.01, max_grad_norm=1.0, bf16=True, fp16=False, gradient_checkpointing=True,
            optim="adamw_bnb_8bit", seed=20260908, data_seed=20260908, logging_steps=1,
            save_strategy="steps", save_steps=100, save_total_limit=2, report_to="none", dataset_num_proc=1,
            per_device_eval_batch_size=1, eval_strategy="no")
        trainer = UnslothTrainer(model=model, processing_class=model_tokenizer, train_dataset=split_data["train"],
            eval_dataset=split_data["dev"], args=settings, callbacks=[Progress()])
        torch.cuda.reset_peak_memory_stats()
        start = time.time()
        result = trainer.train(
            resume_from_checkpoint=(str(args.resume_from_checkpoint) if args.resume_from_checkpoint else None)
        )
        trainer.save_model(str(args.output))
        model_tokenizer.save_pretrained(str(args.output))
        trainer.save_state()
        metrics = {**result.metrics, "elapsed_seconds": time.time()-start,
            "peak_allocated_mib": torch.cuda.max_memory_allocated()/1024**2,
            "adapter_sha256": sha(args.output / "adapter_model.safetensors"), "production_promoted": False}
        write("run_metrics.json", metrics)
        write("status.json", {"stage": "completed", "metrics": metrics})
        print("TRAINING_COMPLETED " + json.dumps(metrics), flush=True)
    except Exception as exc:
        write("status.json", {"stage": "failed", "error": str(exc), "traceback": traceback.format_exc()})
        raise


if __name__ == "__main__":
    main()
