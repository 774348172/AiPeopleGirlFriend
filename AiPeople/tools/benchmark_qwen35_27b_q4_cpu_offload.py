from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil


OLLAMA_URL = "http://127.0.0.1:11434"
MODEL = "qwen3.5:27b"


def _post(path: str, payload: dict[str, Any], timeout: float = 600.0) -> dict[str, Any]:
    request = urllib.request.Request(
        OLLAMA_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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
    )
    return int(completed.stdout.strip().splitlines()[0])


def _ollama_memory() -> tuple[int, int]:
    rss = 0
    private = 0
    for process in psutil.process_iter(["name", "memory_info"]):
        try:
            process_name = (process.info["name"] or "").lower()
            if "ollama" not in process_name and "llama-server" not in process_name:
                continue
            info = process.info["memory_info"]
            rss += int(info.rss)
            private += int(getattr(info, "private", info.rss))
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return rss, private


class Sampler:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.samples: list[dict[str, int]] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            rss, private = _ollama_memory()
            virtual = psutil.virtual_memory()
            try:
                gpu_used = _gpu_used_mib()
            except (OSError, subprocess.SubprocessError, ValueError):
                gpu_used = -1
            self.samples.append(
                {
                    "gpu_used_mib": gpu_used,
                    "system_used_bytes": int(virtual.used),
                    "ollama_rss_bytes": rss,
                    "ollama_private_bytes": private,
                }
            )
            self.stop_event.wait(0.1)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)

    def summary(self) -> dict[str, float | int]:
        valid_gpu = [sample["gpu_used_mib"] for sample in self.samples if sample["gpu_used_mib"] >= 0]
        return {
            "sample_count": len(self.samples),
            "gpu_peak_total_mib": max(valid_gpu, default=-1),
            "system_ram_peak_used_gib": round(
                max((sample["system_used_bytes"] for sample in self.samples), default=0) / 1024**3,
                3,
            ),
            "ollama_peak_rss_gib": round(
                max((sample["ollama_rss_bytes"] for sample in self.samples), default=0) / 1024**3,
                3,
            ),
            "ollama_peak_private_gib": round(
                max((sample["ollama_private_bytes"] for sample in self.samples), default=0) / 1024**3,
                3,
            ),
        }


def _unload() -> None:
    subprocess.run(
        ["ollama", "stop", MODEL],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    for _ in range(60):
        completed = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if MODEL not in completed.stdout:
            return
        time.sleep(0.25)
    raise RuntimeError(f"timed out unloading {MODEL}")


def _prompt() -> list[dict[str, str]]:
    filler = "\n- 旧日记录只用于模拟上下文长度，不得覆盖当前权威状态。" * 70
    return [
        {
            "role": "system",
            "content": (
                "你是白未晞。只输出简短对白，不输出动作旁白。"
                "当前权威状态：蓝色保温壶在书桌左侧抽屉，厨房置物架是旧位置。"
                + filler
            ),
        },
        {"role": "user", "content": "保温壶还在厨房架上，对吗？"},
    ]


def _run_level(num_gpu: int, parallel: int) -> dict[str, Any]:
    _unload()
    time.sleep(1)
    baseline_gpu_mib = _gpu_used_mib()
    baseline_system_used = psutil.virtual_memory().used
    sampler = Sampler()
    sampler.start()
    started = time.perf_counter()
    def request_one(index: int) -> dict[str, Any]:
        return _post(
            "/api/chat",
            {
            "model": MODEL,
            "messages": _prompt(),
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "num_ctx": 4096,
                "num_predict": 64,
                "temperature": 0.75,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "seed": 20260825 + num_gpu + index,
                "num_gpu": num_gpu,
            },
        },
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        payloads = list(executor.map(request_one, range(parallel)))
    elapsed = time.perf_counter() - started
    time.sleep(1)
    sampler.stop()
    summary = sampler.summary()
    ps_output = subprocess.run(
        ["ollama", "ps"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    ).stdout.strip()
    payload = payloads[0]
    messages = [
        value.get("message") if isinstance(value.get("message"), dict) else {}
        for value in payloads
    ]
    total_eval_count = sum(int(value.get("eval_count", 0)) for value in payloads)
    total_eval_duration = sum(float(value.get("eval_duration", 0)) for value in payloads)
    result = {
        "num_gpu": num_gpu,
        "parallel_requests": parallel,
        "status": "completed",
        "baseline_gpu_total_mib": baseline_gpu_mib,
        "gpu_peak_model_delta_mib": summary["gpu_peak_total_mib"] - baseline_gpu_mib,
        "baseline_system_ram_used_gib": round(baseline_system_used / 1024**3, 3),
        **summary,
        "elapsed_seconds_including_load": round(elapsed, 3),
        "load_seconds": round(float(payload.get("load_duration", 0)) / 1e9, 3),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count_total": total_eval_count,
        "eval_tokens_per_second": round(
            total_eval_count / max(total_eval_duration / 1e9, 1e-9),
            3,
        ),
        "responses": [str(message.get("content", "")).strip() for message in messages],
        "ollama_ps": ps_output,
    }
    _unload()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure Qwen3.5-27B Q4 partial GPU offload memory")
    parser.add_argument("output", type=Path)
    parser.add_argument("--num-gpu", type=int, nargs="+", default=[32, 36, 40])
    parser.add_argument("--parallel", type=int, default=1)
    args = parser.parse_args()
    if any(value < 0 or value > 64 for value in args.num_gpu):
        raise ValueError("num-gpu must be between 0 and 64")
    if not 1 <= args.parallel <= 8:
        raise ValueError("parallel must be between 1 and 8")

    results = []
    for num_gpu in args.num_gpu:
        result = _run_level(num_gpu, args.parallel)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "model": MODEL,
        "model_identity": "Qwen3.5-27B Q4_K_M base; Baiweixi LoRA is not merged into this Ollama artifact",
        "controls": {
            "num_ctx": 4096,
            "num_predict": 64,
            "prompt": "approximately 600+ tokens with authoritative-state correction",
            "num_gpu_levels": args.num_gpu,
            "parallel_requests": args.parallel,
        },
        "results": results,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
