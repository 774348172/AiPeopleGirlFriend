"""Run a gated Gemma 4 12B 9216-token QLoRA training stage."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if "--disable-unsloth-compile" in sys.argv:
    os.environ.setdefault("UNSLOTH_COMPILE_DISABLE", "1")

# Unsloth must patch Transformers and TRL before either is imported.
import unsloth  # noqa: E402,F401

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
CREATE_ROOT = WORKSPACE_ROOT / "AiPeopleCreate"
if str(CREATE_ROOT) not in sys.path:
    sys.path.insert(0, str(CREATE_ROOT))

from run_gemma_v2_9216_probe import (  # noqa: E402
    EXPECTED_TARGETS,
    GpuMonitor,
    TARGET_NAMES,
    gpu_used_mib,
    make_example,
    read_json,
    sha_file,
    write_json,
)


DEFAULT_PACKAGE = (
    ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b_v2_2611_9216"
)
DEFAULT_PROBE = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v2_2611_9216_sdpa_probe"
)
DEFAULT_OUTPUT = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v2_2611_9216_stage_b_step25_20260921"
)
OPTIMIZED_PACKAGE = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b_optimized_v2_2611_6144"
OPTIMIZED_PROBE = (
    ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs"
    / "baiweixi_v2_2611_9216_accum1_stability_20260922"
)
OPTIMIZED_OUTPUT = (
    ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs"
    / "baiweixi_optimized_v2_2611_full_20260922"
)
OPTIMIZED_FULL_OUTPUT = (
    ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs"
    / "baiweixi_optimized_v2_2611_epoch1_20260922"
)
SEED = 20260921
ALLOWED_STAGE_STEPS = {25, 50, 100, 200}


def runtime_model_path(value: str) -> str:
    """Translate a frozen Windows manifest path when running under WSL."""
    if os.name == "nt":
        return value
    match = re.fullmatch(r"([A-Za-z]):\\(.*)", value)
    if not match:
        return value
    return f"/mnt/{match[1].lower()}/{match[2].replace(chr(92), '/') }"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def verify_package(package: Path, optimized: bool = False) -> dict[str, Any]:
    manifest = read_json(package / "manifest.json")
    if (
        manifest.get("status") != "immutable_probe_ready"
        or manifest.get("probe_authorized") is not True
        or manifest.get("full_training_authorized") is not False
        or manifest.get("max_seq_length") != (6144 if optimized else 9216)
        or manifest.get("supervision_policy") != "final_assistant_only"
        or (optimized and manifest.get("render_profile_id") != "GAME_REPLY_CONTEXT_V2_COMPACT")
    ):
        raise ValueError("Frozen package contract differs from the approved package")
    for name, expected in manifest["files"].items():
        if sha_file(package / name) != expected:
            raise ValueError(f"Frozen package file changed: {name}")
    return manifest


def verify_probe(probe: Path, optimized: bool = False) -> dict[str, Any]:
    if optimized:
        metrics = read_json(probe / "run_metrics.json")
        if (
            metrics.get("status") != "passed"
            or metrics.get("scope") != "accum1_multi_step_probe_only"
            or metrics.get("steps_completed") != 6
            or metrics.get("gradient_accumulation_steps") != 1
            or max(item["sequence_tokens"] for item in metrics["step_metrics"]) != 8641
            or metrics.get("gpu_used_peak_mib", 99999) > 24564
        ):
            raise ValueError("Prior accum1 stability evidence differs")
        return {"adapter_sha256": None, "gpu_used_peak_mib": metrics["gpu_used_peak_mib"]}
    status = read_json(probe / "status.json")
    metrics = read_json(probe / "run_metrics.json")
    adapter = probe / "adapter" / "adapter_model.safetensors"
    if status.get("stage") != "probe_complete" or metrics.get("status") != "passed":
        raise ValueError("The optimized 9216-token probe has not passed")
    if (
        metrics.get("scope") != "one_optimizer_step_vram_probe_only"
        or metrics.get("sequence_tokens") != 8641
        or metrics.get("language_target_module_count") != EXPECTED_TARGETS
        or metrics.get("training_continued") is not False
        or metrics.get("full_training_authorized") is not False
    ):
        raise ValueError("Probe evidence differs from the approved contract")
    if not adapter.is_file() or sha_file(adapter) != metrics.get("adapter_sha256"):
        raise ValueError("Probe Adapter is missing or changed")
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stage-max-steps", type=int, default=25)
    parser.add_argument("--resume-from-checkpoint", type=Path)
    parser.add_argument("--resume-interrupted", action="store_true", help="Resume a terminated run after verifying its recorded process is absent.")
    parser.add_argument("--user-authorized", action="store_true")
    parser.add_argument("--max-preexisting-gpu-used-mib", type=int, default=4096)
    parser.add_argument("--optimized-v2", action="store_true")
    parser.add_argument("--candidate-source", type=Path, help="Verified V3 corpus; requires optimized mode and a new explicit output directory.")
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="Optional runtime-only model directory override; does not alter the frozen manifest.",
    )
    parser.add_argument(
        "--disable-unsloth-compile",
        action="store_true",
        help="Disable Unsloth dynamic compilation for the stable Linux training path.",
    )
    args = parser.parse_args()
    requested_output = args.output
    if args.candidate_source is not None:
        if not args.optimized_v2 or requested_output == DEFAULT_OUTPUT:
            raise ValueError("V3 requires optimized mode and an explicit output directory")
        args.output = DEFAULT_OUTPUT
    if args.optimized_v2:
        if args.package != DEFAULT_PACKAGE or args.probe != DEFAULT_PROBE or args.output != DEFAULT_OUTPUT:
            raise ValueError("Optimized V2 uses its own fixed package, probe and output paths")
        if args.stage_max_steps not in {25, 2332}:
            raise ValueError("Optimized V2 supports a 25-step gate or exactly one 2332-step epoch")
        if args.resume_from_checkpoint is not None and args.stage_max_steps != 2332:
            raise ValueError("Only the same full-epoch run can resume from a checkpoint")
        args.package, args.probe = OPTIMIZED_PACKAGE, OPTIMIZED_PROBE
        args.output = OPTIMIZED_FULL_OUTPUT if args.stage_max_steps == 2332 else OPTIMIZED_OUTPUT
        if args.stage_max_steps == 2332:
            gate_status = read_json(OPTIMIZED_OUTPUT / "status.json")
            gate_metrics = read_json(OPTIMIZED_OUTPUT / "run_metrics.json")
            gate_config = read_json(OPTIMIZED_OUTPUT / "training_config.json")
            if (
                gate_status.get("stage") != "stage_b_complete"
                or gate_metrics.get("stage_max_steps") != 25
                or gate_metrics.get("status") != "stage_complete"
                or gate_metrics.get("gpu_used_mib_peak", 99999) > 24564
                or gate_config.get("dataset_manifest_sha256") != sha_file(OPTIMIZED_PACKAGE / "manifest.json")
            ):
                raise ValueError("Optimized 25-step gate or package identity has not passed")
    if args.candidate_source is not None:
        args.output = requested_output
    if not args.user_authorized:
        raise ValueError("Formal training requires the explicit --user-authorized flag")
    if not args.optimized_v2 and args.stage_max_steps not in ALLOWED_STAGE_STEPS:
        raise ValueError(f"Stage must end at one of {sorted(ALLOWED_STAGE_STEPS)}")

    package = args.package.resolve()
    probe = args.probe.resolve()
    output = args.output.resolve()
    resuming = args.resume_from_checkpoint is not None
    if output.exists() and not resuming:
        raise FileExistsError(f"Refusing to replace training output: {output}")
    if resuming:
        checkpoint = args.resume_from_checkpoint.resolve()
        if not args.optimized_v2 or checkpoint.parent != output / "checkpoints" or not checkpoint.is_dir():
            raise ValueError("Checkpoint must belong to this optimized full-epoch run")
        checkpoint_state = read_json(checkpoint / "trainer_state.json")
        if checkpoint.name != f"checkpoint-{checkpoint_state.get('global_step')}":
            raise ValueError("Checkpoint directory and trainer state disagree")
        paused_status = read_json(output / "status.json")
        if args.resume_interrupted:
            if paused_status.get("stage") not in {"stage_b_training", "stage_b_failed"}:
                raise ValueError("Interrupted resume requires an unfinished training run")
            old_pid = paused_status.get("pid")
            if old_pid == os.getpid():
                # The new process may receive the prior run's PID after WSL restarts.
                old_pid = None
            if old_pid and sys.platform == "linux":
                try:
                    old_cmdline = Path(f"/proc/{int(old_pid)}/cmdline").read_bytes()
                except FileNotFoundError:
                    old_pid = None
                else:
                    # A WSL restart can reuse the recorded PID for an unrelated service.
                    old_args = old_cmdline.split(b"\0")
                    if not any(arg.rsplit(b"/", 1)[-1] == b"train_gemma_v2_9216.py" for arg in old_args):
                        old_pid = None
            if old_pid:
                try:
                    os.kill(int(old_pid), 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError("Recorded training process is still present")
            if paused_status.get("global_step", checkpoint_state["global_step"]) < checkpoint_state["global_step"]:
                raise ValueError("Checkpoint is ahead of the recorded run")
        elif paused_status.get("stage") != "paused_by_user" or paused_status.get("resume_checkpoint_step") != checkpoint_state["global_step"]:
            raise ValueError("Run is not paused at this checkpoint")
        previous_config = read_json(output / "training_config.json")
        if (
            previous_config.get("dataset_manifest_sha256") != sha_file(OPTIMIZED_PACKAGE / "manifest.json")
            or previous_config.get("stage_max_steps") != args.stage_max_steps
            or previous_config.get("gradient_accumulation_steps") != 1
        ):
            raise ValueError("Package or optimizer schedule differs from the paused run")
        for required in ("optimizer.pt", "scheduler.pt", "rng_state.pth", "adapter_model.safetensors"):
            if not (checkpoint / required).is_file():
                raise FileNotFoundError(checkpoint / required)
        previous_candidate = previous_config.get("candidate_identity")
        if bool(previous_candidate) != bool(args.candidate_source):
            raise ValueError("Resume must preserve V2/V3 input format")
        if args.candidate_source:
            for filename, key in (("manifest.json", "candidate_manifest_sha256"), ("train.jsonl", "candidate_train_sha256"), ("audit.json", "candidate_audit_sha256")):
                if sha_file(args.candidate_source / filename) != previous_candidate[key]:
                    raise ValueError(f"Resume V3 data changed: {filename}")
        write_json(output / f"resume_evidence_step{checkpoint_state['global_step']}_{time.time_ns()}.json", {"previous_status": paused_status, "previous_config": previous_config, "checkpoint": str(checkpoint)})
    preexisting_gpu_used = gpu_used_mib()
    if (
        preexisting_gpu_used is None
        or preexisting_gpu_used > args.max_preexisting_gpu_used_mib
    ):
        raise RuntimeError(
            "GPU is not clear enough for formal training: "
            f"used={preexisting_gpu_used} MiB, "
            f"limit={args.max_preexisting_gpu_used_mib} MiB"
        )

    manifest = verify_package(package, args.optimized_v2)
    probe_metrics = verify_probe(probe, args.optimized_v2)
    max_length = manifest["max_seq_length"]
    accumulation = 1 if args.optimized_v2 else 8
    output.mkdir(parents=True, exist_ok=resuming)
    authorization = {
        "scope": "stage_b_small_step_gate",
        "authorized_by": "user",
        "authorized_at": date.today().isoformat() if args.optimized_v2 else "2026-09-21",
        "user_instruction": "现在用优化数据V2 来开始训练模型" if args.optimized_v2 else "现在开始训练",
        "stage_max_steps": args.stage_max_steps,
        "full_epoch_authorized": args.optimized_v2 and args.stage_max_steps == manifest["counts"]["train"],
        "production_adapter_replacement_authorized": False,
        "resuming_from": str(checkpoint) if resuming else None,
    }
    write_json(output / "run_authorization.json", authorization)
    write_json(
        output / "status.json",
        {
            "stage": "preparing_training_data",
            "pid": os.getpid(),
            "global_step": 0,
            "target_step": args.stage_max_steps,
        },
    )

    try:
        import torch
        from datasets import Dataset
        from transformers import AutoTokenizer, TrainerCallback
        from trl import SFTConfig
        from unsloth import FastModel
        from unsloth.chat_templates import get_chat_template
        from unsloth.trainer import UnslothTrainer

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable")
        model_path = (
            args.model_path.resolve()
            if args.model_path is not None
            else Path(runtime_model_path(manifest["model_path"]))
        )
        tokenizer = get_chat_template(
            AutoTokenizer.from_pretrained(str(model_path), local_files_only=True),
            chat_template="gemma-4",
        )
        template_sha = hashlib.sha256(tokenizer.chat_template.encode("utf-8")).hexdigest()
        if template_sha != manifest["chat_template_sha256"]:
            raise ValueError("Training chat template differs from the frozen package")
        rows = read_jsonl(package / "data" / "train.jsonl")
        if len(rows) != manifest["counts"]["train"]:
            raise ValueError("Frozen train row count changed")
        candidate_info = None
        if args.candidate_source is not None:
            from profile_gemma_optimized_v2_pause import load_candidate_rows
            rows, candidate_info = load_candidate_rows(args.candidate_source.resolve(), rows)
            candidate_info["render_profile"] = "GAME_REPLY_CONTEXT_V3_COMPACT"
        examples: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            example, span = make_example(
                tokenizer, row, max_length=max_length
            )
            if span.token_count != row["supervised_tokens"]:
                raise ValueError(f"{row['sample_id']}: supervision span changed")
            examples.append(example)
            if index % 100 == 0:
                write_json(
                    output / "status.json",
                    {
                        "stage": "preparing_training_data",
                        "pid": os.getpid(),
                        "prepared_rows": index + 1,
                        "total_rows": len(rows),
                        "global_step": 0,
                        "target_step": args.stage_max_steps,
                    },
                )
        dataset = Dataset.from_list(examples)

        write_json(
            output / "training_config.json",
            {
                "scope": "optimized_v2_one_epoch_with_25_step_gate" if args.optimized_v2 else "stage_b_small_step_gate",
                "base_model": str(model_path),
                "base_revision": manifest["base_revision"],
                "dataset_manifest_sha256": sha_file(package / "manifest.json"),
                "train_rows": len(rows),
                "candidate_source": str(args.candidate_source.resolve()) if args.candidate_source else None,
                "candidate_identity": candidate_info,
                "max_seq_length": max_length,
                "truncation": False,
                "packing": False,
                "supervision": "final_assistant_only",
                "quantization": "bitsandbytes NF4",
                "attention_implementation": "sdpa",
                "lora_rank": 8,
                "lora_alpha": 16,
                "lora_dropout": 0.0,
                "learning_rate": 1e-5,
                "micro_batch_size": 1,
                "gradient_accumulation_steps": accumulation,
                "stage_max_steps": args.stage_max_steps,
                "seed": SEED,
                "probe_adapter_sha256": probe_metrics["adapter_sha256"],
                "gate_run_metrics_sha256": sha_file(OPTIMIZED_OUTPUT / "run_metrics.json") if args.optimized_v2 and args.stage_max_steps == 2332 else None,
                "runtime_alignment_pending": args.optimized_v2,
                "clean_base_only": True,
            },
        )

        write_json(
            output / "status.json",
            {
                "stage": "loading_clean_base",
                "pid": os.getpid(),
                "prepared_rows": len(rows),
                "global_step": 0,
                "target_step": args.stage_max_steps,
            },
        )
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.manual_seed(SEED)
        torch.cuda.manual_seed_all(SEED)
        model, loaded_tokenizer = FastModel.from_pretrained(
            model_name=str(model_path),
            max_seq_length=max_length,
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
            raise ValueError("Loaded model template differs from package preflight")
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

        class StatusCallback(TrainerCallback):
            def on_log(self, _args: Any, state: Any, _control: Any, logs: Any = None, **_kwargs: Any) -> None:
                write_json(
                    output / "status.json",
                    {
                        "stage": "stage_b_training",
                        "pid": os.getpid(),
                        "global_step": state.global_step,
                        "target_step": args.stage_max_steps,
                        "latest_log": logs or {},
                    },
                )

        settings = SFTConfig(
            output_dir=str(output / "checkpoints"),
            dataset_text_field="text",
            max_length=max_length,
            packing=False,
            padding_free=False,
            per_device_train_batch_size=1,
            gradient_accumulation_steps=accumulation,
            learning_rate=1e-5,
            num_train_epochs=1.0,
            max_steps=args.stage_max_steps,
            lr_scheduler_type="cosine",
            warmup_steps=max(1, round(args.stage_max_steps * 0.05)),
            weight_decay=0.01,
            max_grad_norm=1.0,
            bf16=True,
            fp16=False,
            gradient_checkpointing=True,
            optim="adamw_bnb_8bit",
            seed=SEED,
            data_seed=SEED,
            logging_steps=1,
            save_strategy="steps",
            save_steps=25 if args.optimized_v2 else 25,
            save_total_limit=4,
            report_to="none",
            dataset_num_proc=1,
        )
        trainer = UnslothTrainer(
            model=model,
            processing_class=loaded_tokenizer,
            train_dataset=dataset,
            args=settings,
            callbacks=[StatusCallback()],
        )
        write_json(
            output / "status.json",
            {
                "stage": "stage_b_training",
                "pid": os.getpid(),
                "global_step": 0,
                "target_step": args.stage_max_steps,
            },
        )
        torch.cuda.reset_peak_memory_stats()
        started = time.time()
        with GpuMonitor() as monitor:
            result = trainer.train(
                resume_from_checkpoint=(
                    str(args.resume_from_checkpoint.resolve())
                    if args.resume_from_checkpoint
                    else None
                )
            )
        elapsed = time.time() - started
        adapter = output / f"adapter-step{args.stage_max_steps}"
        trainer.save_model(str(adapter))
        loaded_tokenizer.save_pretrained(str(adapter))
        trainer.save_state()
        adapter_path = adapter / "adapter_model.safetensors"
        metrics = {
            **result.metrics,
            "status": "stage_complete",
            "scope": "optimized_v2_one_epoch_with_25_step_gate" if args.optimized_v2 else "stage_b_small_step_gate",
            "stage_max_steps": args.stage_max_steps,
            "elapsed_seconds_measured": elapsed,
            "gpu_used_mib_peak": max(monitor.samples) if monitor.samples else None,
            "torch_peak_allocated_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "torch_peak_reserved_mib": torch.cuda.max_memory_reserved() / 1024**2,
            "trainable_parameters": trainable,
            "language_target_module_count": len(targets),
            "adapter_sha256": sha_file(adapter_path),
            "full_epoch_completed": args.optimized_v2 and args.stage_max_steps == manifest["counts"]["train"],
            "production_adapter_replaced": False,
        }
        write_json(output / "run_metrics.json", metrics)
        write_json(output / "status.json", {"stage": "stage_b_complete", "metrics": metrics})
        print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)
        return 0
    except BaseException as error:
        write_json(
            output / "status.json",
            {
                "stage": "stage_b_failed",
                "error": str(error),
                "traceback": traceback.format_exc(),
                "production_adapter_replaced": False,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
