"""Run exactly one worst-case 9216-token Gemma 4 QLoRA optimizer step."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import traceback
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Unsloth must patch Transformers/TRL before either library is imported.
import unsloth  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
CREATE_ROOT = WORKSPACE_ROOT / "AiPeopleCreate"
if str(CREATE_ROOT) not in sys.path:
    sys.path.insert(0, str(CREATE_ROOT))

from data_gen_v4.adapters.modes.training import TrainingRecordV4  # noqa: E402
from data_gen_v4.runtime_grounded.renderer import (  # noqa: E402
    GAME_REPLY_CONTEXT_RENDER_PROFILE,
    IGNORE_INDEX,
    build_current_assistant_example,
)

DEFAULT_PACKAGE = (
    ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b_v2_2611_9216"
)
DEFAULT_OUTPUT = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v2_2611_9216_probe"
)
TARGET_NAMES = {"q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"}
EXPECTED_TARGETS = 328
SEED = 20260921


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def gpu_used_mib() -> int | None:
    try:
        value = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).splitlines()[0]
        return int(value.strip())
    except Exception:
        return None


class GpuMonitor:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.samples: list[int] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            used = gpu_used_mib()
            if used is not None:
                self.samples.append(used)
            self.stop_event.wait(0.25)

    def __enter__(self) -> "GpuMonitor":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)


def load_longest(package: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    wanted = manifest["longest_sequence"]["sample_id"]
    for split in ("train", "dev"):
        with (package / "data" / f"{split}.jsonl").open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["sample_id"] == wanted:
                    return row
    raise ValueError(f"Longest sample not found: {wanted}")


def make_example(tokenizer: Any, row: dict[str, Any], max_length: int) -> tuple[dict[str, Any], Any]:
    messages = row["messages"]
    if row.get("supervised_message_indexes") != [len(messages) - 1]:
        raise ValueError("Package row does not supervise only final assistant")
    record = TrainingRecordV4(
        sample_id=row["sample_id"],
        mode="GAME_REPLY",
        render_profile_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        messages=[dict(message) for message in messages[1:]],
        supervised_message_indexes=[len(messages) - 2],
        supervised_token_count=0,
        protocol_snapshot_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        system_anchor=messages[0]["content"],
        split=row["split"],
    )
    return build_current_assistant_example(
        tokenizer, record, max_seq_length=max_length, row_index=0
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--max-preexisting-gpu-used-mib", type=int, default=4096)
    args = parser.parse_args()
    if args.max_steps != 1:
        raise ValueError("This probe is hard-limited to exactly one optimizer step")
    package, output = args.package.resolve(), args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to replace probe output: {output}")
    preexisting_gpu_used = gpu_used_mib()
    if (
        preexisting_gpu_used is None
        or preexisting_gpu_used > args.max_preexisting_gpu_used_mib
    ):
        raise RuntimeError(
            "GPU is not clear enough for a reproducible VRAM probe: "
            f"used={preexisting_gpu_used} MiB, "
            f"limit={args.max_preexisting_gpu_used_mib} MiB"
        )
    output.mkdir(parents=True)
    write_json(output / "status.json", {"stage": "validating_package", "pid": os.getpid()})

    try:
        manifest = read_json(package / "manifest.json")
        if (
            manifest.get("status") != "immutable_probe_ready"
            or manifest.get("probe_authorized") is not True
            or manifest.get("full_training_authorized") is not False
            or manifest.get("supervision_policy") != "final_assistant_only"
            or manifest.get("max_seq_length") != 9216
        ):
            raise ValueError("Package is not authorized for the one-step 9216 probe")
        for name, expected in manifest["files"].items():
            if sha_file(package / name) != expected:
                raise ValueError(f"Frozen package file changed: {name}")

        import torch
        from unsloth import FastModel
        from unsloth.chat_templates import get_chat_template
        from unsloth.trainer import UnslothTrainer
        from datasets import Dataset
        from transformers import AutoTokenizer
        from trl import SFTConfig

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        model_path = Path(manifest["model_path"])
        tokenizer = get_chat_template(
            AutoTokenizer.from_pretrained(str(model_path), local_files_only=True),
            chat_template="gemma-4",
        )
        template_sha = hashlib.sha256(tokenizer.chat_template.encode("utf-8")).hexdigest()
        if template_sha != manifest["chat_template_sha256"]:
            raise ValueError("Probe chat template differs from frozen package")
        row = load_longest(package, manifest)
        example, span = make_example(tokenizer, row, manifest["max_seq_length"])
        if len(example["input_ids"]) != manifest["longest_sequence"]["sequence_tokens"]:
            raise ValueError("Worst-case sequence token count changed")
        if any(label != IGNORE_INDEX for label in example["labels"][: span.start]):
            raise RuntimeError("Prompt tokens entered the probe loss mask")
        dataset = Dataset.from_list([example])
        write_json(
            output / "probe_input.json",
            {
                "sample_id": row["sample_id"],
                "split": row["split"],
                "sequence_tokens": len(example["input_ids"]),
                "supervised_tokens": span.token_count,
                "training_pair_sha256": row["training_pair_sha256"],
            },
        )
        write_json(
            output / "training_config.json",
            {
                "scope": "one_optimizer_step_vram_probe_only",
                "max_steps": 1,
                "base_model": str(model_path),
                "base_revision": manifest["base_revision"],
                "max_seq_length": 9216,
                "quantization": "bitsandbytes NF4",
                "lora_rank": 8,
                "lora_alpha": 16,
                "lora_dropout": 0.0,
                "attention_implementation": "sdpa",
                "learning_rate": 1e-5,
                "gradient_accumulation_steps": 1,
                "full_training_authorized": False,
            },
        )

        write_json(output / "status.json", {"stage": "loading_model", "pid": os.getpid()})
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.manual_seed(SEED)
        torch.cuda.manual_seed_all(SEED)
        gpu_before = gpu_used_mib()
        model, loaded_tokenizer = FastModel.from_pretrained(
            model_name=str(model_path),
            max_seq_length=9216,
            dtype=torch.bfloat16,
            load_in_4bit=True,
            text_only=True,
            trust_remote_code=True,
            use_exact_model_name=True,
            use_gradient_checkpointing="unsloth",
            random_state=SEED,
            attn_implementation="sdpa",
        )
        loaded_tokenizer = get_chat_template(loaded_tokenizer, chat_template="gemma-4")
        if loaded_tokenizer.chat_template != tokenizer.chat_template:
            raise ValueError("Loaded model template differs from preflight template")
        targets = sorted(
            name
            for name, _module in model.named_modules()
            if "language_model.layers." in name
            and name.rsplit(".", 1)[-1] in TARGET_NAMES
        )
        if len(targets) != EXPECTED_TARGETS:
            raise RuntimeError(f"Unexpected Gemma language LoRA targets: {len(targets)}")
        model = FastModel.get_peft_model(
            model,
            r=8,
            target_modules=targets,
            lora_alpha=16,
            lora_dropout=0.0,
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=SEED,
        )
        unexpected = [
            name
            for name, parameter in model.named_parameters()
            if parameter.requires_grad and "language_model.layers." not in name
        ]
        if unexpected:
            raise RuntimeError(f"Non-language parameters became trainable: {unexpected[:5]}")
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)

        settings = SFTConfig(
            output_dir=str(output),
            dataset_text_field="text",
            max_length=9216,
            packing=False,
            padding_free=False,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=1,
            learning_rate=1e-5,
            num_train_epochs=1.0,
            max_steps=1,
            lr_scheduler_type="constant",
            warmup_ratio=0.0,
            weight_decay=0.01,
            max_grad_norm=1.0,
            bf16=True,
            fp16=False,
            gradient_checkpointing=True,
            optim="adamw_bnb_8bit",
            seed=SEED,
            data_seed=SEED,
            logging_steps=1,
            save_strategy="no",
            report_to="none",
            dataset_num_proc=1,
        )
        trainer = UnslothTrainer(
            model=model,
            processing_class=loaded_tokenizer,
            train_dataset=dataset,
            args=settings,
        )
        torch.cuda.reset_peak_memory_stats()
        write_json(output / "status.json", {"stage": "one_step_probe", "pid": os.getpid()})
        started = time.time()
        with GpuMonitor() as monitor:
            result = trainer.train()
        elapsed = time.time() - started
        trainer.save_model(str(output / "adapter"))
        loaded_tokenizer.save_pretrained(str(output / "adapter"))
        adapter_path = output / "adapter" / "adapter_model.safetensors"
        metrics = {
            **result.metrics,
            "status": "passed",
            "scope": "one_optimizer_step_vram_probe_only",
            "training_continued": False,
            "full_training_authorized": False,
            "sample_id": row["sample_id"],
            "sequence_tokens": len(example["input_ids"]),
            "supervised_tokens": span.token_count,
            "elapsed_seconds_measured": elapsed,
            "gpu_used_mib_before_load": gpu_before,
            "gpu_used_mib_peak_during_step": max(monitor.samples) if monitor.samples else None,
            "gpu_used_mib_after_step": gpu_used_mib(),
            "torch_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "torch_peak_reserved_mib": torch.cuda.max_memory_reserved() / 1024**2,
            "trainable_parameters": trainable,
            "language_target_module_count": len(targets),
            "adapter_sha256": sha_file(adapter_path),
        }
        write_json(output / "run_metrics.json", metrics)
        write_json(output / "status.json", {"stage": "probe_complete", "metrics": metrics})
        print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
        return 0
    except Exception as error:
        write_json(
            output / "status.json",
            {
                "stage": "probe_failed",
                "error": str(error),
                "traceback": traceback.format_exc(),
                "full_training_authorized": False,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
