from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import psutil


ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
FROZEN_INPUTS = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "qwen35_27b_gpt56_60turn_20260824-114447"
    / "shared_inputs.jsonl"
)
EXPECTED_INPUT_SHA256 = "0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720"
HISTORY_LIMIT_MESSAGES = 6
SEED_BASE = 2026082200

if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import run_baiweixi_60turn_player_simulation as simulation
import run_baiweixi_paired_raw_context_ab as paired
import run_low_vram_lora_60turn_comparison as common


def _system_memory_used_mib() -> float | None:
    memory = psutil.virtual_memory()
    return round(float(memory.used) / (1024 * 1024), 2)


def _gpu_memory_used_mib() -> float | None:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
        return float(output.splitlines()[0].strip())
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


class ResourceSampler:
    def __init__(self) -> None:
        self.samples: list[dict[str, float]] = []
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            gpu = await asyncio.to_thread(_gpu_memory_used_mib)
            ram = await asyncio.to_thread(_system_memory_used_mib)
            sample: dict[str, float] = {"elapsed_s": time.perf_counter()}
            if gpu is not None:
                sample["gpu_used_mib"] = gpu
            if ram is not None:
                sample["system_ram_used_mib"] = ram
            self.samples.append(sample)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    def summary(self) -> dict[str, Any]:
        gpu = [sample["gpu_used_mib"] for sample in self.samples if "gpu_used_mib" in sample]
        ram = [sample["system_ram_used_mib"] for sample in self.samples if "system_ram_used_mib" in sample]
        return {
            "sample_interval_seconds": 1.0,
            "samples": len(self.samples),
            "gpu_baseline_mib": gpu[0] if gpu else None,
            "gpu_peak_mib": max(gpu) if gpu else None,
            "gpu_peak_delta_mib": round(max(gpu) - gpu[0], 2) if gpu else None,
            "system_ram_baseline_mib": ram[0] if ram else None,
            "system_ram_peak_mib": max(ram) if ram else None,
            "system_ram_peak_delta_mib": round(max(ram) - ram[0], 2) if ram else None,
        }


async def _run_arm_with_resources(
    client: httpx.AsyncClient,
    *,
    key: str,
    label: str,
    model: str,
    digest: str,
    inputs: list[dict[str, Any]],
    output_dir: Path,
    limit: int,
    thinking: bool,
    num_ctx: int,
    num_predict: int,
    temperature: float = 0.75,
    top_p: float = 0.9,
    repeat_penalty: float = 1.1,
) -> dict[str, Any]:
    sampler = ResourceSampler()
    task = asyncio.create_task(sampler.run())
    try:
        arm = await common._run_arm(
            client,
            key,
            label,
            model,
            digest,
            inputs,
            output_dir,
            limit,
            thinking,
            num_ctx,
            num_predict,
            temperature,
            top_p,
            repeat_penalty,
        )
    finally:
        sampler.stop()
        await task
    arm["thinking_requested"] = thinking
    arm["resources"] = sampler.summary()
    return arm


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if common._sha256(FROZEN_INPUTS) != EXPECTED_INPUT_SHA256:
        raise ValueError("frozen 60-turn input SHA256 mismatch")
    inputs = common._load_jsonl(FROZEN_INPUTS)
    common._validate_inputs(inputs)
    common._write_jsonl(output_dir / "shared_inputs.jsonl", inputs)

    async with httpx.AsyncClient(
        base_url=args.ollama_url.rstrip("/"), timeout=httpx.Timeout(600.0)
    ) as client:
        digests = await paired._model_digests(client)
        if args.model not in digests:
            raise RuntimeError(f"Ollama model is not installed: {args.model}")
        await common._unload(client, args.model)
        normal = await _run_arm_with_resources(
            client,
            key="normal",
            label="Gemma 4 12B Q4 普通模式",
            model=args.model,
            digest=digests[args.model],
            inputs=inputs,
            output_dir=output_dir,
            limit=args.limit,
            thinking=False,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict_normal,
        )
        await common._unload(client, args.model)
        thinking = await _run_arm_with_resources(
            client,
            key="thinking",
            label="Gemma 4 12B Q4 思考模式",
            model=args.model,
            digest=digests[args.model],
            inputs=inputs,
            output_dir=output_dir,
            limit=args.limit,
            thinking=True,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict_thinking,
        )

    if args.limit != 60:
        probe = {
            "status": "probe_completed",
            "turns_per_arm": args.limit,
            "normal": normal,
            "thinking": thinking,
        }
        (output_dir / "probe.json").write_text(
            json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(probe, ensure_ascii=False, indent=2))
        return 0

    report_turns = []
    for input_turn, source, turn_a, turn_b in zip(
        inputs, simulation.TURNS, normal["turns"], thinking["turns"], strict=True
    ):
        report_turns.append(
            {
                "turn": input_turn["ordinal"],
                "phase": input_turn["phase"],
                "player": input_turn["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "normal": turn_a,
                "thinking": turn_b,
            }
        )

    report = {
        "schema_version": 1,
        "scope": "gemma4_12b_normal_vs_thinking_stateful_60turn",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "completed",
        "controls": {
            "turn_count": 60,
            "history_limit_messages": HISTORY_LIMIT_MESSAGES,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "each_arm_retains_own_history": True,
            "arm_order": ["normal", "thinking"],
            "only_intentional_difference": "Ollama think=false versus think=true",
            "generation": {
                "temperature": 0.75,
                "top_p": 0.9,
                "repeat_penalty": 1.1,
                "num_ctx": args.num_ctx,
                "num_predict_normal": args.num_predict_normal,
                "num_predict_thinking": args.num_predict_thinking,
                "seed_base": SEED_BASE,
                "num_gpu": 999,
            },
            "latency_definitions": {
                "first_output_ms": "request start to first thinking or visible content token",
                "first_visible_ms": "request start to first non-whitespace player-visible answer token",
                "elapsed_ms": "request start to completed response",
                "warm_metrics": "turns 2-60; excludes each arm's cold model-load turn",
            },
            "limitations": [
                "Each arm keeps its own generated history after turn 1, matching an actual multi-turn product trajectory.",
                "The arms run sequentially in fixed order; warm metrics remove the cold-load turn but not every order effect.",
                "This trajectory supports paired human review and is not a population-level accuracy estimate.",
                "System RAM is whole-machine usage, so its delta may include unrelated background processes.",
            ],
        },
        "arms": {"normal": normal, "thinking": thinking},
        "turns": report_turns,
        "summary": {
            "turns": 60,
            "normal_completed": len(normal["turns"]),
            "thinking_completed": len(thinking["turns"]),
            "normal_generation_failures": sum(
                bool(turn.get("generation_error")) for turn in normal["turns"]
            ),
            "thinking_generation_failures": sum(
                bool(turn.get("generation_error")) for turn in thinking["turns"]
            ),
            "speed": {
                "normal": common._speed_summary(normal["turns"]),
                "thinking": common._speed_summary(thinking["turns"]),
            },
            "resources": {
                "normal": normal["resources"],
                "thinking": thinking["resources"],
            },
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": report["generated_at"],
                "report_sha256": common._sha256(report_path),
                "input_sha256": EXPECTED_INPUT_SHA256,
                "model_digest": normal["ollama_digest"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"REPORT={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Gemma 4 12B normal/thinking paired 60-turn test")
    parser.add_argument("output_dir")
    parser.add_argument("--model", default="gemma4:12b")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--num-predict-normal", type=int, default=2048)
    parser.add_argument("--num-predict-thinking", type=int, default=2048)
    args = parser.parse_args()
    if not 1 <= args.limit <= 60:
        raise ValueError("--limit must be between 1 and 60")
    if min(args.num_ctx, args.num_predict_normal, args.num_predict_thinking) < 1:
        raise ValueError("context and output budgets must be positive")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
