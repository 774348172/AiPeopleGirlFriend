"""Bounded isolated resume profile from the paused optimized Gemma run."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import traceback
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# This must be set before importing Unsloth.  The Linux probe can opt out of
# dynamic compilation when compiler/allocator overhead dominates the long
# context training step.
if "--disable-unsloth-compile" in sys.argv:
    os.environ.setdefault("UNSLOTH_COMPILE_DISABLE", "1")
import unsloth  # noqa: E402,F401

from run_gemma_v2_9216_probe import (  # noqa: E402
    EXPECTED_TARGETS, TARGET_NAMES, gpu_used_mib, make_example, read_json, sha_file, write_json,
)
from train_gemma_v2_9216 import OPTIMIZED_FULL_OUTPUT, OPTIMIZED_PACKAGE, verify_package

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs" / "optimized_v2_pause_memory_profile_counters_20260922"
CHECKPOINT = OPTIMIZED_FULL_OUTPUT / "checkpoints" / "checkpoint-125"
STEPS = 4
SEED = 20260921
V3_CANDIDATE = ROOT.parent / "AiPeopleCreate" / "优化数据V3候选"


def runtime_model_path(value: str) -> str:
    """Translate the frozen Windows manifest path when this probe runs in WSL."""
    if os.name == "nt":
        return value
    match = re.fullmatch(r"([A-Za-z]):\\(.*)", value)
    if not match:
        return value
    return f"/mnt/{match[1].lower()}/{match[2].replace(chr(92), '/') }"


def load_candidate_rows(candidate: Path, original_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sys.path.insert(0, str(ROOT))
    from runtime.world_mind.compact_game_reply_context_v3 import encode_system_v3

    manifest = read_json(candidate / "manifest.json")
    audit = read_json(candidate / "audit.json")
    if (
        manifest.get("status") != "experimental_candidate"
        or manifest.get("training_authorized") is not False
        or audit.get("rows_verified") != 2611
        or audit.get("v2_system_byte_roundtrips") != 2611
        or audit.get("dialogue_and_completion_unchanged") != 2611
    ):
        raise ValueError("V3 candidate audit contract differs")
    for name in ("train", "metadata"):
        if sha_file(candidate / f"{name}.jsonl") != manifest[f"{name}_sha256"]:
            raise ValueError(f"V3 {name} hash drift")
    if sha_file(candidate / "audit.json") != manifest["audit_sha256"]:
        raise ValueError("V3 audit hash drift")
    with (candidate / "train.jsonl").open(encoding="utf-8") as handle:
        pairs = [json.loads(line) for line in handle]
    with (candidate / "metadata.jsonl").open(encoding="utf-8") as handle:
        metadata = [json.loads(line) for line in handle]
    if len(pairs) != len(metadata) or len(pairs) != 2611:
        raise ValueError("V3 candidate count drift")
    originals = {row["sample_id"]: row for row in original_rows}
    result: dict[str, dict[str, Any]] = {}
    for index, (pair, meta) in enumerate(zip(pairs, metadata, strict=True), 1):
        sid = meta["sample_id"]
        if meta["line"] != index or sid in result:
            raise ValueError(f"V3 candidate identity drift: {sid}")
        if sid not in originals:
            continue  # Dev split is verified by the frozen package and corpus audit.
        old = originals[sid]
        messages = [*pair["prompt"], *pair["completion"]]
        if (
            len(messages) != len(old["messages"])
            or messages[1:] != old["messages"][1:]
            or messages[0]["content"] != encode_system_v3(old["messages"][0]["content"])
        ):
            raise ValueError(f"V3 input/target ancestry drift: {sid}")
        row = dict(old)
        row["messages"] = messages
        result[sid] = row
    if len(result) != len(original_rows) or set(result) != set(originals):
        raise ValueError("V3 train split coverage drift")
    return [result[row["sample_id"]] for row in original_rows], {
        "candidate_manifest_sha256": sha_file(candidate / "manifest.json"),
        "candidate_train_sha256": manifest["train_sha256"],
        "candidate_audit_sha256": manifest["audit_sha256"],
        "render_profile": "GAME_REPLY_CONTEXT_V3_COMPACT_PROBE_ONLY",
    }


def process_gpu_memory(pid: int) -> dict[str, float | None]:
    if os.name != "nt":
        values: dict[str, float | None] = {"shared_mib": None, "dedicated_mib": None}
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_gpu_memory", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8, check=True,
            )
            for line in result.stdout.splitlines():
                fields = [value.strip() for value in line.split(",")]
                if len(fields) == 2 and fields[0].isdigit() and int(fields[0]) == pid:
                    values["dedicated_mib"] = round(float(fields[1]), 1)
                    break
        except Exception:
            pass
        return values
    command = (
        "$samples=(Get-Counter -Counter '\\GPU Process Memory(*)\\Shared Usage',"
        "'\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction Stop).CounterSamples;"
        f"$samples | Where-Object {{$_.Path -match 'pid_{pid}_'}} | "
        "Select-Object Path,CookedValue | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=8, check=True,
    )
    values: dict[str, float | None] = {"shared_mib": None, "dedicated_mib": None}
    if not result.stdout.strip():
        return values
    items = json.loads(result.stdout)
    for item in items if isinstance(items, list) else [items]:
        path = item["Path"].lower()
        if "luid_0x00000000_0x0000d6fb" not in path:
            continue
        name = "shared_mib" if "shared usage" in path else "dedicated_mib"
        values[name] = round(float(item["CookedValue"]) / 1024**2, 1)
    if all(value is None for value in values.values()):
        raise ValueError(f"GPU process counter did not match adapter: {result.stdout[:500]}")
    return values


class ResourceSampler:
    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        query = "memory.used,memory.free,utilization.gpu,power.draw,clocks.sm,clocks.mem,pstate,temperature.gpu"
        while not self.stop.is_set():
            sample: dict[str, Any] = {"unix_time": time.time()}
            try:
                line = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=" + query, "--format=csv,noheader,nounits"],
                    text=True, timeout=5,
                ).splitlines()[0]
                fields = [value.strip() for value in line.split(",")]
                for name, value in zip(query.split(","), fields, strict=True):
                    sample[name] = value
                sample.update(process_gpu_memory(os.getpid()))
            except Exception as error:
                sample["sample_error"] = str(error)
            self.samples.append(sample)
            self.stop.wait(3)

    def __enter__(self) -> "ResourceSampler":
        self.worker.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.worker.join(timeout=12)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--steps", type=int, default=STEPS)
    parser.add_argument("--slow-threshold-seconds", type=float, default=60.0)
    parser.add_argument("--slow-consecutive", type=int, default=3)
    parser.add_argument("--min-steps", type=int, default=30)
    parser.add_argument("--empty-cache-after-step", action="store_true")
    parser.add_argument("--checkpoint", type=Path, default=CHECKPOINT)
    parser.add_argument("--candidate-source", type=Path, default=None)
    parser.add_argument("--max-seq-length", type=int, default=6144)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="Optional runtime-only model directory override; does not alter the frozen manifest.",
    )
    parser.add_argument(
        "--disable-unsloth-compile",
        action="store_true",
        help="Disable Unsloth dynamic compilation for a reproducible Linux runtime path.",
    )
    parser.add_argument("--save-checkpoint-at-stop", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.steps <= 90 or args.slow_consecutive < 1 or args.min_steps < 0 or args.max_seq_length < 1024:
        raise ValueError("Invalid bounded profile settings")
    output = args.output.resolve()
    checkpoint = args.checkpoint.resolve()
    if output.exists():
        raise FileExistsError(output)
    if (gpu_used_mib() or 99999) > 4096:
        raise RuntimeError("GPU preoccupied")
    manifest = verify_package(OPTIMIZED_PACKAGE, optimized=True)
    paused = read_json(OPTIMIZED_FULL_OUTPUT / "status.json")
    state = read_json(checkpoint / "trainer_state.json")
    start_step = state.get("global_step")
    if paused.get("stage") != "paused_by_user" or not isinstance(start_step, int) or not 125 <= start_step < 2332:
        raise ValueError("Expected paused step-125 checkpoint")
    if start_step + args.steps > 2332:
        raise ValueError("Profile would exceed the one-epoch schedule")
    if checkpoint != CHECKPOINT.resolve():
        prior_report = read_json(checkpoint.parent.parent / "report.json")
        if (
            checkpoint.parent.name != "trainer"
            or prior_report.get("scope") != "isolated_resume_profile_only"
            or prior_report.get("stop_step") != start_step
            or prior_report.get("saved_checkpoint") != str(checkpoint)
        ):
            raise ValueError("Diagnostic checkpoint does not match its prior segment")
    for required in ("adapter_model.safetensors", "optimizer.pt", "scheduler.pt", "rng_state.pth"):
        if not (checkpoint / required).is_file():
            raise FileNotFoundError(checkpoint / required)
    checkpoints = {p.name: sha_file(p) for p in checkpoint.iterdir() if p.is_file()}
    output.mkdir(parents=True)
    write_json(output / "status.json", {"stage": "preparing", "pid": os.getpid(), "checkpoint_step": start_step, "max_additional_steps": args.steps, "max_seq_length": args.max_seq_length})
    try:
        import torch
        from datasets import Dataset
        from transformers import AutoTokenizer, TrainerCallback
        from trl import SFTConfig
        from unsloth import FastModel
        from unsloth.chat_templates import get_chat_template
        from unsloth.trainer import UnslothTrainer

        model_path = str(args.model_path.resolve()) if args.model_path is not None else runtime_model_path(manifest["model_path"])
        tokenizer = get_chat_template(
            AutoTokenizer.from_pretrained(model_path, local_files_only=True),
            chat_template="gemma-4",
        )
        if hashlib.sha256(tokenizer.chat_template.encode()).hexdigest() != manifest["chat_template_sha256"]:
            raise ValueError("Template drift")
        with (OPTIMIZED_PACKAGE / "data" / "train.jsonl").open(encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        if len(rows) != 2332:
            raise ValueError("Train count drift")
        candidate_info: dict[str, Any] | None = None
        if args.candidate_source is not None:
            if args.candidate_source.resolve() != V3_CANDIDATE.resolve():
                raise ValueError("Only the audited V3 candidate is supported")
            rows, candidate_info = load_candidate_rows(V3_CANDIDATE, rows)
        examples = []
        for row in rows:
            example, span = make_example(tokenizer, row, max_length=args.max_seq_length)
            if (
                (candidate_info is None and len(example["input_ids"]) != row["sequence_tokens"])
                or len(example["input_ids"]) > args.max_seq_length
                or span.token_count != row["supervised_tokens"]
            ):
                raise ValueError(f"Supervision changed: {row['sample_id']}")
            examples.append(example)
        dataset = Dataset.from_list(examples)
        write_json(output / "status.json", {"stage": "loading", "pid": os.getpid(), "checkpoint_step": start_step, "max_additional_steps": args.steps, "max_seq_length": args.max_seq_length})
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.manual_seed(SEED)
        torch.cuda.manual_seed_all(SEED)
        model, loaded_tokenizer = FastModel.from_pretrained(
            model_name=model_path, max_seq_length=args.max_seq_length,
            dtype=torch.bfloat16, load_in_4bit=True, text_only=True,
            trust_remote_code=True, use_exact_model_name=True,
            use_gradient_checkpointing="unsloth", random_state=SEED,
            attn_implementation="sdpa",
        )
        loaded_tokenizer = get_chat_template(loaded_tokenizer, chat_template="gemma-4")
        if loaded_tokenizer.chat_template != tokenizer.chat_template:
            raise ValueError("Loaded template drift")
        targets = sorted(
            name for name, _ in model.named_modules()
            if "language_model.layers." in name and name.rsplit(".", 1)[-1] in TARGET_NAMES
        )
        if len(targets) != EXPECTED_TARGETS:
            raise ValueError("Language LoRA target drift")
        model = FastModel.get_peft_model(
            model, r=8, target_modules=targets, lora_alpha=16,
            lora_dropout=0.0, bias="none", use_gradient_checkpointing="unsloth",
            random_state=SEED,
        )
        if any(p.requires_grad and "language_model.layers." not in name for name, p in model.named_parameters()):
            raise ValueError("Unexpected trainable parameter")

        step_start = 0.0
        sample_start = 0
        measurements: list[dict[str, Any]] = []
        sampler: ResourceSampler | None = None
        stop_reason = "max_steps"

        class ProfileCallback(TrainerCallback):
            def on_step_begin(self, _args: Any, state: Any, _control: Any, **_kwargs: Any) -> None:
                nonlocal step_start, sample_start
                step_start = time.monotonic()
                sample_start = len(sampler.samples) if sampler else 0
                torch.cuda.reset_peak_memory_stats()
                write_json(output / "status.json", {"stage": "profiling", "pid": os.getpid(), "completed_step": state.global_step, "stop_at": start_step + args.steps, "max_seq_length": args.max_seq_length})

            def on_step_end(self, _args: Any, state: Any, control: Any, **_kwargs: Any) -> Any:
                nonlocal stop_reason
                recent = sampler.samples[sample_start:] if sampler else []
                measurements.append({
                    "step": state.global_step,
                    "seconds": round(time.monotonic() - step_start, 2),
                    "gpu_used_peak_mib": max((float(x.get("memory.used", 0)) for x in recent), default=None),
                    "shared_peak_mib": max((x["shared_mib"] for x in recent if x.get("shared_mib") is not None), default=None),
                    "dedicated_peak_mib": max((x["dedicated_mib"] for x in recent if x.get("dedicated_mib") is not None), default=None),
                    "torch_allocated_peak_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 1),
                    "torch_reserved_peak_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 1),
                })
                if args.empty_cache_after_step:
                    release_start = time.monotonic()
                    torch.cuda.empty_cache()
                    measurements[-1]["cache_release_seconds"] = round(time.monotonic() - release_start, 2)
                    measurements[-1]["reserved_after_cache_release_mib"] = round(torch.cuda.memory_reserved() / 1024**2, 1)
                write_json(output / "steps.json", measurements)
                write_json(output / "resource_samples.json", sampler.samples if sampler else [])
                is_slow = (
                    len(measurements) >= args.min_steps
                    and len(measurements) >= args.slow_consecutive
                    and all(item["seconds"] >= args.slow_threshold_seconds for item in measurements[-args.slow_consecutive:])
                )
                if is_slow:
                    stop_reason = "sustained_slowdown"
                if args.save_checkpoint_at_stop and state.global_step == start_step + args.steps:
                    control.should_save = True
                if is_slow or state.global_step >= start_step + args.steps:
                    control.should_training_stop = True
                return control

        settings = SFTConfig(
            output_dir=str(output / "trainer"), dataset_text_field="text",
            max_length=args.max_seq_length, packing=False, padding_free=False,
            per_device_train_batch_size=1, gradient_accumulation_steps=1,
            learning_rate=1e-5, num_train_epochs=1.0, max_steps=2332,
            lr_scheduler_type="cosine", warmup_steps=117, weight_decay=0.01,
            max_grad_norm=1.0, bf16=True, fp16=False,
            gradient_checkpointing=True, optim="adamw_bnb_8bit",
            seed=SEED, data_seed=SEED, logging_steps=1,
            save_strategy="steps" if args.save_checkpoint_at_stop else "no",
            save_steps=start_step + args.steps if args.save_checkpoint_at_stop else 500,
            save_total_limit=1, report_to="none", dataset_num_proc=1,
        )
        trainer = UnslothTrainer(
            model=model, processing_class=loaded_tokenizer,
            train_dataset=dataset, args=settings, callbacks=[ProfileCallback()],
        )
        with ResourceSampler() as active:
            sampler = active
            if candidate_info is None:
                result = trainer.train(resume_from_checkpoint=str(checkpoint))
            else:
                import numpy as np
                from numpy._core.multiarray import _reconstruct

                unsafe = set(torch.serialization.get_unsafe_globals_in_checkpoint(checkpoint / "rng_state.pth"))
                if unsafe != {"numpy._core.multiarray._reconstruct", "numpy.dtype", "numpy.ndarray"}:
                    raise ValueError(f"Unexpected RNG checkpoint globals: {sorted(unsafe)}")
                with torch.serialization.safe_globals([
                    (_reconstruct, "numpy._core.multiarray._reconstruct"),
                    np.dtype, np.ndarray, type(np.dtype(np.uint32)),
                ]):
                    result = trainer.train(resume_from_checkpoint=str(checkpoint))
        if not measurements or len(measurements) > args.steps or (
            stop_reason == "max_steps" and measurements[-1]["step"] != start_step + args.steps
        ):
            raise ValueError("Profile did not stop at its bounded condition")
        if {p.name: sha_file(p) for p in checkpoint.iterdir() if p.is_file()} != checkpoints:
            raise ValueError("Paused source checkpoint changed")
        saved_checkpoint = output / "trainer" / f"checkpoint-{start_step + args.steps}"
        if args.save_checkpoint_at_stop and (
            stop_reason != "max_steps" or not (saved_checkpoint / "optimizer.pt").is_file()
            or read_json(saved_checkpoint / "trainer_state.json").get("global_step") != start_step + args.steps
        ):
            raise ValueError("Segment checkpoint was not saved completely")
        write_json(output / "resource_samples.json", sampler.samples)
        report = {
            "scope": "isolated_resume_profile_only", "original_run_untouched": True,
            "checkpoint_step": start_step, "stop_step": measurements[-1]["step"],
            "stop_reason": stop_reason, "requested_additional_steps": args.steps,
            "max_seq_length": args.max_seq_length,
            "empty_cache_after_step": args.empty_cache_after_step,
            "saved_checkpoint": str(saved_checkpoint) if args.save_checkpoint_at_stop else None,
            "source_checkpoint_files": checkpoints,
            "candidate": candidate_info,
            "steps": measurements, "samples": len(sampler.samples),
            "train_runtime": result.metrics.get("train_runtime"),
            "profile_finished_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(output / "report.json", report)
        write_json(output / "status.json", {"stage": "profile_complete", "completed_step": measurements[-1]["step"], "max_seq_length": args.max_seq_length, "original_run_untouched": True})
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    except BaseException as error:
        write_json(output / "status.json", {"stage": "profile_failed", "error": str(error), "traceback": traceback.format_exc(), "original_run_untouched": True})
        raise


if __name__ == "__main__":
    main()
