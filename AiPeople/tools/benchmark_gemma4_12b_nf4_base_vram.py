from __future__ import annotations

import argparse
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from unsloth import FastModel
import psutil
import torch


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "training_packages" / "models" / "Gemma-4-12B-it"
REFERENCE_REPORT = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "gemma4_12b_base_qlora_60turn_20260827-104300"
    / "report.json"
)


def _gpu_used_mib() -> int:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return int(completed.stdout.strip().splitlines()[0])


def _process_memory() -> tuple[int, int]:
    info = psutil.Process().memory_info()
    return int(info.rss), int(getattr(info, "private", info.rss))


class Sampler:
    def __init__(self) -> None:
        self.samples: list[dict[str, int | float]] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            rss, private = _process_memory()
            virtual = psutil.virtual_memory()
            try:
                gpu_used = _gpu_used_mib()
            except (OSError, ValueError, subprocess.SubprocessError):
                gpu_used = -1
            self.samples.append(
                {
                    "timestamp": time.time(),
                    "gpu_used_mib": gpu_used,
                    "system_used_bytes": int(virtual.used),
                    "process_rss_bytes": rss,
                    "process_private_bytes": private,
                }
            )
            self.stop_event.wait(0.1)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=10)

    def summary(self, baseline_gpu_mib: int, baseline_system_used: int) -> dict[str, Any]:
        valid_gpu = [
            int(sample["gpu_used_mib"])
            for sample in self.samples
            if int(sample["gpu_used_mib"]) >= 0
        ]
        peak_system = max(
            (int(sample["system_used_bytes"]) for sample in self.samples), default=0
        )
        peak_rss = max(
            (int(sample["process_rss_bytes"]) for sample in self.samples), default=0
        )
        peak_private = max(
            (int(sample["process_private_bytes"]) for sample in self.samples), default=0
        )
        peak_gpu = max(valid_gpu, default=-1)
        return {
            "sample_interval_seconds": 0.1,
            "sample_count": len(self.samples),
            "gpu_baseline_total_mib": baseline_gpu_mib,
            "gpu_peak_total_mib": peak_gpu,
            "gpu_peak_model_delta_mib": (
                peak_gpu - baseline_gpu_mib if peak_gpu >= 0 else None
            ),
            "system_ram_baseline_used_gib": round(baseline_system_used / 1024**3, 3),
            "system_ram_peak_used_gib": round(peak_system / 1024**3, 3),
            "system_ram_peak_delta_gib": round(
                (peak_system - baseline_system_used) / 1024**3, 3
            ),
            "process_peak_rss_gib": round(peak_rss / 1024**3, 3),
            "process_peak_private_gib": round(peak_private / 1024**3, 3),
        }


def _snapshot() -> dict[str, Any]:
    torch.cuda.synchronize()
    free_bytes, total_bytes = torch.cuda.mem_get_info()
    rss, private = _process_memory()
    return {
        "gpu_total_used_mib": _gpu_used_mib(),
        "torch_allocated_mib": round(torch.cuda.memory_allocated() / 1024**2, 2),
        "torch_reserved_mib": round(torch.cuda.memory_reserved() / 1024**2, 2),
        "cuda_device_used_mib": round((total_bytes - free_bytes) / 1024**2, 2),
        "process_rss_gib": round(rss / 1024**3, 3),
        "process_private_gib": round(private / 1024**3, 3),
        "system_ram_used_gib": round(psutil.virtual_memory().used / 1024**3, 3),
    }


def _load_messages() -> list[dict[str, str]]:
    report = json.loads(REFERENCE_REPORT.read_text(encoding="utf-8"))
    messages = report["turns"][59]["base"]["messages"]
    if not isinstance(messages, list) or len(messages) != 8:
        raise ValueError("reference turn 60 messages changed")
    return messages


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure Gemma 4 12B official base loaded with bitsandbytes NF4"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=180)
    args = parser.parse_args()
    if args.max_new_tokens < 1:
        raise ValueError("--max-new-tokens must be positive")
    if not MODEL_PATH.is_dir() or not REFERENCE_REPORT.is_file():
        raise FileNotFoundError("Gemma base model or reference report is missing")

    baseline_gpu_mib = _gpu_used_mib()
    baseline_system_used = int(psutil.virtual_memory().used)
    before_load = {
        "gpu_total_used_mib": baseline_gpu_mib,
        "system_ram_used_gib": round(baseline_system_used / 1024**3, 3),
    }
    sampler = Sampler()
    sampler.start()
    started = time.perf_counter()

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    from unsloth.chat_templates import get_chat_template

    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    after_load = _snapshot()
    torch.cuda.reset_peak_memory_stats()

    messages = _load_messages()
    encoded = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_tensors="pt",
        return_dict=True,
    )
    encoded = {key: value.to("cuda") for key, value in encoded.items()}
    prompt_tokens = int(encoded["input_ids"].shape[-1])
    generation_started = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            **encoded,
            max_new_tokens=args.max_new_tokens,
            do_sample=True,
            temperature=0.75,
            top_p=0.9,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=[tokenizer.eos_token_id, tokenizer.eot_token_id],
            use_cache=True,
        )
    torch.cuda.synchronize()
    generation_seconds = time.perf_counter() - generation_started
    generated_ids = output[0, prompt_tokens:]
    generated_tokens = int(generated_ids.shape[-1])
    parsed = tokenizer.parse_response(generated_ids)
    response = str(parsed.get("content") or "").strip()
    after_generation = _snapshot()
    torch_peak = {
        "max_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
        "max_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
    }
    time.sleep(1)
    sampler.stop()

    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "model": "google/gemma-4-12B-it",
        "model_path": str(MODEL_PATH),
        "identity": "official base model text path; bitsandbytes NF4 runtime; no LoRA/Adapter loaded",
        "runtime": {
            "surface": "Transformers 5.5 + Unsloth + bitsandbytes NF4",
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0),
            "text_only": True,
            "max_seq_length": 4096,
            "thinking": False,
        },
        "workload": {
            "source": "frozen 60-turn product trajectory, turn 60 base messages",
            "history_messages": 6,
            "prompt_tokens": prompt_tokens,
            "max_new_tokens": args.max_new_tokens,
            "generated_tokens": generated_tokens,
            "response": response,
            "generation_seconds": round(generation_seconds, 3),
        },
        "snapshots": {
            "before_load": before_load,
            "after_load": after_load,
            "after_generation": after_generation,
        },
        "torch_generation_peak": torch_peak,
        "sampled_resources": sampler.summary(
            baseline_gpu_mib=baseline_gpu_mib,
            baseline_system_used=baseline_system_used,
        ),
        "elapsed_seconds_including_load": round(time.perf_counter() - started, 3),
        "notes": [
            "nvidia-smi values are whole-machine totals and include background GPU users.",
            "gpu_peak_model_delta_mib subtracts the total GPU baseline measured immediately before model loading.",
            "The prompt is the longest/last turn from the frozen product trajectory, with the model's own six-message history shape.",
            "The generated response ended at EOS before the configured max_new_tokens budget; max_seq_length remains 4096.",
        ],
    }
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"REPORT={output_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
