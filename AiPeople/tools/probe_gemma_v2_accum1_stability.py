"""Isolated fixed-order multi-step Gemma 4 VRAM probe; never runs full training."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import unsloth  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from run_gemma_v2_9216_probe import (  # noqa: E402
    DEFAULT_PACKAGE, EXPECTED_TARGETS, GpuMonitor, TARGET_NAMES,
    gpu_used_mib, make_example, read_json, sha_file, write_json,
)

OUTPUT = (
    ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b"
    / "outputs" / "baiweixi_v2_2611_9216_accum1_stability_20260922"
)
SEED = 20260922
TARGET_LENGTHS = [4553, 6632, 7492, 8559, 8641]
STEPS = 6  # The longest sample is repeated consecutively.


def select_rows(package: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with (package / "data" / "train.jsonl").open(encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in TARGET_LENGTHS[:-1]:
        row = min(
            (item for item in rows if item["sample_id"] not in seen),
            key=lambda item: (abs(item["sequence_tokens"] - target), item["sample_id"]),
        )
        seen.add(row["sample_id"])
        selected.append(row)
    longest = next(item for item in rows if item["sample_id"] == manifest["longest_sequence"]["sample_id"])
    selected.extend([longest, longest])
    return selected


def main() -> None:
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite probe: {OUTPUT}")
    before = gpu_used_mib()
    if before is None or before > 4096:
        raise RuntimeError(f"GPU preoccupied: {before} MiB")
    package = DEFAULT_PACKAGE.resolve()
    manifest = read_json(package / "manifest.json")
    if manifest.get("max_seq_length") != 9216 or manifest.get("full_training_authorized") is not False:
        raise ValueError("Unexpected frozen package")
    for name, expected in manifest["files"].items():
        if sha_file(package / name) != expected:
            raise ValueError(f"Frozen file changed: {name}")
    chosen = select_rows(package, manifest)
    if len(chosen) != STEPS or chosen[-1]["sequence_tokens"] != 8641:
        raise ValueError("Invalid probe selections")
    OUTPUT.mkdir(parents=True)
    write_json(OUTPUT / "status.json", {"stage": "preparing", "pid": os.getpid(), "completed_steps": 0, "max_steps": STEPS})
    try:
        import torch
        from torch.utils.data import SequentialSampler
        from datasets import Dataset
        from transformers import AutoTokenizer, TrainerCallback
        from trl import SFTConfig
        from unsloth import FastModel
        from unsloth.chat_templates import get_chat_template
        from unsloth.trainer import UnslothTrainer

        tokenizer = get_chat_template(
            AutoTokenizer.from_pretrained(manifest["model_path"], local_files_only=True),
            chat_template="gemma-4",
        )
        if hashlib.sha256(tokenizer.chat_template.encode("utf-8")).hexdigest() != manifest["chat_template_sha256"]:
            raise ValueError("Chat template drift")
        examples = []
        selections = []
        for row in chosen:
            example, span = make_example(tokenizer, row, 9216)
            if len(example["input_ids"]) != row["sequence_tokens"] or span.token_count != row["supervised_tokens"]:
                raise ValueError(f"Changed frozen example: {row['sample_id']}")
            examples.append(example)
            selections.append({"sample_id": row["sample_id"], "sequence_tokens": row["sequence_tokens"], "supervised_tokens": span.token_count})
        write_json(OUTPUT / "probe_inputs.json", {"ordered_samples": selections, "gradient_accumulation_steps": 1, "max_steps": STEPS, "full_training": False})
        dataset = Dataset.from_list(examples)
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.manual_seed(SEED)
        torch.cuda.manual_seed_all(SEED)
        write_json(OUTPUT / "status.json", {"stage": "loading_clean_base", "pid": os.getpid(), "completed_steps": 0, "max_steps": STEPS})
        model, loaded_tokenizer = FastModel.from_pretrained(
            model_name=manifest["model_path"], max_seq_length=9216,
            dtype=torch.bfloat16, load_in_4bit=True, text_only=True,
            trust_remote_code=True, use_exact_model_name=True,
            use_gradient_checkpointing="unsloth", random_state=SEED,
            attn_implementation="sdpa",
        )
        loaded_tokenizer = get_chat_template(loaded_tokenizer, chat_template="gemma-4")
        if loaded_tokenizer.chat_template != tokenizer.chat_template:
            raise ValueError("Model template drift")
        targets = sorted(
            name for name, _module in model.named_modules()
            if "language_model.layers." in name and name.rsplit(".", 1)[-1] in TARGET_NAMES
        )
        if len(targets) != EXPECTED_TARGETS:
            raise ValueError(f"Unexpected LoRA modules: {len(targets)}")
        model = FastModel.get_peft_model(
            model, r=8, target_modules=targets, lora_alpha=16, lora_dropout=0.0,
            bias="none", use_gradient_checkpointing="unsloth", random_state=SEED,
        )
        if any(p.requires_grad and "language_model.layers." not in name for name, p in model.named_parameters()):
            raise ValueError("Non-language LoRA parameter is trainable")

        step_metrics: list[dict[str, Any]] = []
        current_start = 0.0
        sample_start = 0
        monitor: GpuMonitor | None = None

        class OrderedTrainer(UnslothTrainer):
            def _get_train_sampler(self, train_dataset: Any = None) -> Any:
                return SequentialSampler(train_dataset if train_dataset is not None else self.train_dataset)

        class ProbeCallback(TrainerCallback):
            def on_step_begin(self, _args: Any, state: Any, _control: Any, **_kwargs: Any) -> None:
                nonlocal current_start, sample_start
                current_start = time.monotonic()
                sample_start = len(monitor.samples) if monitor else 0
                torch.cuda.reset_peak_memory_stats()
                write_json(OUTPUT / "status.json", {"stage": "probing", "pid": os.getpid(), "completed_steps": state.global_step, "current_sample": selections[state.global_step], "max_steps": STEPS})

            def on_step_end(self, _args: Any, state: Any, _control: Any, **_kwargs: Any) -> None:
                samples = monitor.samples[sample_start:] if monitor else []
                entry = {
                    "step": state.global_step, **selections[state.global_step - 1],
                    "seconds": round(time.monotonic() - current_start, 2),
                    "gpu_used_peak_mib": max(samples) if samples else None,
                    "torch_peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
                    "torch_peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 1),
                }
                step_metrics.append(entry)
                write_json(OUTPUT / "steps.json", step_metrics)
                write_json(OUTPUT / "status.json", {"stage": "probing", "pid": os.getpid(), "completed_steps": state.global_step, "max_steps": STEPS, "last_step": entry})

            def on_log(self, _args: Any, state: Any, _control: Any, logs: Any = None, **_kwargs: Any) -> None:
                if step_metrics and logs and "loss" in logs:
                    step_metrics[-1]["loss"] = logs["loss"]
                    write_json(OUTPUT / "steps.json", step_metrics)

        settings = SFTConfig(
            output_dir=str(OUTPUT / "trainer"), dataset_text_field="text",
            max_length=9216, packing=False, padding_free=False,
            per_device_train_batch_size=1, gradient_accumulation_steps=1,
            learning_rate=1e-5, num_train_epochs=1.0, max_steps=STEPS,
            lr_scheduler_type="constant", warmup_steps=0, weight_decay=0.01,
            max_grad_norm=1.0, bf16=True, fp16=False,
            gradient_checkpointing=True, optim="adamw_bnb_8bit",
            seed=SEED, data_seed=SEED, logging_steps=1,
            save_strategy="no", report_to="none", dataset_num_proc=1,
        )
        trainer = OrderedTrainer(model=model, processing_class=loaded_tokenizer,
            train_dataset=dataset, args=settings, callbacks=[ProbeCallback()])
        write_json(OUTPUT / "status.json", {"stage": "probing", "pid": os.getpid(), "completed_steps": 0, "max_steps": STEPS})
        with GpuMonitor() as active_monitor:
            monitor = active_monitor
            result = trainer.train()
        if len(step_metrics) != STEPS:
            raise RuntimeError(f"Incomplete step metrics: {len(step_metrics)}")
        metrics = {
            "status": "passed", "scope": "accum1_multi_step_probe_only",
            "steps_completed": STEPS, "gradient_accumulation_steps": 1,
            "same_frozen_data": True, "production_adapter_replaced": False,
            "full_training_started": False, "train_runtime": result.metrics.get("train_runtime"),
            "train_loss": result.metrics.get("train_loss"),
            "gpu_used_peak_mib": max(monitor.samples) if monitor.samples else None,
            "step_metrics": step_metrics,
        }
        write_json(OUTPUT / "run_metrics.json", metrics)
        write_json(OUTPUT / "status.json", {"stage": "probe_complete", "completed_steps": STEPS, "max_steps": STEPS})
        print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
    except BaseException as error:
        write_json(OUTPUT / "status.json", {
            "stage": "probe_failed", "error": str(error),
            "traceback": traceback.format_exc(), "steps_completed": len(step_metrics) if "step_metrics" in locals() else 0,
            "full_training_started": False,
        })
        raise


if __name__ == "__main__":
    main()
