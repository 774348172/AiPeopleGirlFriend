from __future__ import annotations

import argparse
import asyncio
import json
import statistics
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
import run_low_vram_lora_60turn_comparison as common


def _system_memory_used_mib() -> float:
    return round(float(psutil.virtual_memory().used) / (1024 * 1024), 2)


def _gpu_sample() -> dict[str, float]:
    output = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=memory.used,memory.free,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=5,
    )
    used, free, utilization = output.splitlines()[0].split(",")
    return {
        "gpu_used_mib": float(used.strip()),
        "gpu_free_mib": float(free.strip()),
        "gpu_utilization_percent": float(utilization.strip()),
    }


class ResourceSampler:
    def __init__(self) -> None:
        self.samples: list[dict[str, float]] = []
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            sample: dict[str, float] = {
                "elapsed_s": time.perf_counter(),
                "system_ram_used_mib": _system_memory_used_mib(),
            }
            try:
                sample.update(await asyncio.to_thread(_gpu_sample))
            except (OSError, ValueError, subprocess.SubprocessError, IndexError):
                pass
            self.samples.append(sample)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=0.5)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    def summary(self) -> dict[str, Any]:
        def values(key: str) -> list[float]:
            return [sample[key] for sample in self.samples if key in sample]

        gpu_used = values("gpu_used_mib")
        gpu_util = values("gpu_utilization_percent")
        ram_used = values("system_ram_used_mib")
        return {
            "sample_interval_seconds": 0.5,
            "samples": len(self.samples),
            "gpu_baseline_mib": gpu_used[0] if gpu_used else None,
            "gpu_peak_mib": max(gpu_used) if gpu_used else None,
            "gpu_peak_delta_during_gate_mib": (
                round(max(gpu_used) - gpu_used[0], 2) if gpu_used else None
            ),
            "gpu_mean_utilization_percent": (
                round(statistics.fmean(gpu_util), 2) if gpu_util else None
            ),
            "gpu_peak_utilization_percent": max(gpu_util) if gpu_util else None,
            "system_ram_baseline_mib": ram_used[0] if ram_used else None,
            "system_ram_peak_mib": max(ram_used) if ram_used else None,
            "system_ram_peak_delta_during_gate_mib": (
                round(max(ram_used) - ram_used[0], 2) if ram_used else None
            ),
        }


def _messages(turn: dict[str, Any], history: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": turn["system"]},
        *history[-HISTORY_LIMIT_MESSAGES:],
        {"role": "user", "content": turn["player"]},
    ]


async def _generate(
    client: httpx.AsyncClient,
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    top_p: float,
    top_k: int,
) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {
            "enable_thinking": False,
            "preserve_thinking": False,
        },
        "thinking": {"type": "disabled"},
    }
    started = time.perf_counter()
    first_output_ms: float | None = None
    first_visible_ms: float | None = None
    first_reasoning_ms: float | None = None
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    usage: dict[str, Any] = {}
    finish_reason: str | None = None

    async with client.stream("POST", "/v1/chat/completions", json=body) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            chunk = json.loads(data)
            if chunk.get("error"):
                raise RuntimeError(str(chunk["error"]))
            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            now_ms = (time.perf_counter() - started) * 1000
            reasoning = delta.get("reasoning_content")
            content = delta.get("content")
            if isinstance(reasoning, str) and reasoning:
                if first_output_ms is None:
                    first_output_ms = now_ms
                if first_reasoning_ms is None and reasoning.strip():
                    first_reasoning_ms = now_ms
                reasoning_parts.append(reasoning)
            if isinstance(content, str) and content:
                if first_output_ms is None:
                    first_output_ms = now_ms
                if first_visible_ms is None and content.strip():
                    first_visible_ms = now_ms
                content_parts.append(content)

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response_text = "".join(content_parts).strip()
    reasoning_text = "".join(reasoning_parts).strip()
    if not response_text:
        raise RuntimeError("FreeToken returned an empty player-visible response")
    completion_tokens = int(usage.get("completion_tokens") or 0)
    decode_seconds = (
        max((elapsed_ms - first_visible_ms) / 1000, 0.001)
        if first_visible_ms is not None
        else None
    )
    return {
        "response": response_text,
        "thinking": reasoning_text or None,
        "thinking_disabled_verified": (
            not reasoning_text
            and "<think>" not in response_text
            and "</think>" not in response_text
        ),
        "include_response_in_history": True,
        "generation_error": None,
        "elapsed_ms": elapsed_ms,
        "first_output_ms": round(first_output_ms, 2) if first_output_ms is not None else None,
        "first_visible_ms": round(first_visible_ms, 2) if first_visible_ms is not None else None,
        "first_reasoning_ms": (
            round(first_reasoning_ms, 2) if first_reasoning_ms is not None else None
        ),
        "thinking_chars": len(reasoning_text),
        "response_chars": len(response_text),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "completion_tokens_per_second_after_first_visible": (
            round(completion_tokens / decode_seconds, 2)
            if completion_tokens and decode_seconds
            else None
        ),
        "finish_reason": finish_reason,
    }


def _mean(turns: list[dict[str, Any]], key: str, *, skip_first: bool = False) -> float | None:
    source = turns[1:] if skip_first else turns
    values = [float(turn[key]) for turn in source if turn.get(key) is not None]
    return round(statistics.fmean(values), 2) if values else None


def _speed_summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    first_visible = sorted(
        float(turn["first_visible_ms"])
        for turn in turns
        if turn.get("first_visible_ms") is not None
    )
    p90 = None
    if first_visible:
        position = (len(first_visible) - 1) * 0.9
        lower = int(position)
        upper = min(lower + 1, len(first_visible) - 1)
        p90 = round(
            first_visible[lower]
            + (first_visible[upper] - first_visible[lower]) * (position - lower),
            2,
        )
    return {
        "turns": len(turns),
        "generation_failures": sum(bool(turn.get("generation_error")) for turn in turns),
        "thinking_turns": sum(bool(turn.get("thinking")) for turn in turns),
        "thinking_disabled_verified_turns": sum(
            bool(turn.get("thinking_disabled_verified")) for turn in turns
        ),
        "mean_total_elapsed_ms": _mean(turns, "elapsed_ms"),
        "warm_mean_total_elapsed_ms": _mean(turns, "elapsed_ms", skip_first=True),
        "mean_first_visible_ms": _mean(turns, "first_visible_ms"),
        "warm_mean_first_visible_ms": _mean(turns, "first_visible_ms", skip_first=True),
        "p90_first_visible_ms": p90,
        "mean_completion_tokens_per_second": _mean(
            turns, "completion_tokens_per_second_after_first_visible"
        ),
    }


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if common._sha256(FROZEN_INPUTS) != EXPECTED_INPUT_SHA256:
        raise ValueError("frozen 60-turn input SHA256 mismatch")
    inputs = common._load_jsonl(FROZEN_INPUTS)
    common._validate_inputs(inputs)
    common._write_jsonl(output_dir / "shared_inputs.jsonl", inputs)

    partial_path = output_dir / "qwen36_35b_a3b_nvfp4_responses.partial.jsonl"
    turns = common._load_jsonl(partial_path) if partial_path.is_file() else []
    history: list[dict[str, str]] = []
    for source, completed in zip(inputs, turns):
        if completed.get("ordinal") != source["ordinal"] or completed.get("model") != args.model:
            raise ValueError(f"invalid partial result in {partial_path}")
        history.append({"role": "user", "content": source["player"]})
        if completed.get("include_response_in_history", True):
            history.append({"role": "assistant", "content": completed["response"]})

    sampler = ResourceSampler()
    sampler_task = asyncio.create_task(sampler.run())
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)
    ) as client:
        models_response = await client.get("/v1/models")
        models_response.raise_for_status()
        models = [item["id"] for item in models_response.json().get("data", [])]
        if args.model not in models:
            raise RuntimeError(f"FreeToken model is not served: {args.model}; available={models}")

        try:
            for turn in inputs[: args.limit][len(turns) :]:
                ordinal = int(turn["ordinal"])
                messages = _messages(turn, history)
                prompt = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
                try:
                    generated = await _generate(
                        client,
                        model=args.model,
                        messages=messages,
                        max_tokens=args.max_tokens,
                        temperature=args.temperature,
                        top_p=args.top_p,
                        top_k=args.top_k,
                    )
                except Exception as exc:
                    generated = {
                        "response": f"[生成失败：{type(exc).__name__}: {exc}]",
                        "thinking": None,
                        "thinking_disabled_verified": False,
                        "include_response_in_history": False,
                        "generation_error": f"{type(exc).__name__}: {exc}",
                        "elapsed_ms": None,
                        "first_output_ms": None,
                        "first_visible_ms": None,
                        "first_reasoning_ms": None,
                        "thinking_chars": 0,
                        "response_chars": 0,
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "completion_tokens_per_second_after_first_visible": None,
                        "finish_reason": None,
                    }
                record = {
                    "arm": "qwen36_35b_a3b_nvfp4_freetoken",
                    "model": args.model,
                    "ordinal": ordinal,
                    "messages": messages,
                    "prompt_sha256": common.paired._sha256_text(prompt),
                    "seed_requested": SEED_BASE + ordinal,
                    "seed_supported_by_runtime": False,
                    **generated,
                }
                turns.append(record)
                history.append({"role": "user", "content": turn["player"]})
                if generated["include_response_in_history"]:
                    history.append({"role": "assistant", "content": generated["response"]})
                common._write_jsonl(partial_path, turns)
                print(
                    json.dumps(
                        {
                            "turn": ordinal,
                            "elapsed_ms": generated["elapsed_ms"],
                            "first_visible_ms": generated["first_visible_ms"],
                            "response": generated["response"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        finally:
            sampler.stop()
            await sampler_task

    report_turns = []
    for input_turn, source, result in zip(
        inputs[: args.limit], simulation.TURNS[: args.limit], turns, strict=True
    ):
        report_turns.append(
            {
                "turn": input_turn["ordinal"],
                "phase": input_turn["phase"],
                "player": input_turn["player"],
                "authoritative_world": source["world"],
                "expectation": source["expectation"],
                "candidate": result,
            }
        )

    report = {
        "schema_version": 1,
        "scope": "qwen36_35b_a3b_nvfp4_freetoken_hard_gate",
        "generated_at": datetime.now().astimezone().isoformat(),
        "status": "probe_completed_pending_manual_gate",
        "controls": {
            "turn_count": args.limit,
            "history_limit_messages": HISTORY_LIMIT_MESSAGES,
            "same_frozen_oracle_free_inputs": True,
            "input_sha256": EXPECTED_INPUT_SHA256,
            "each_arm_retains_own_history": True,
            "native_model_chat_template": True,
            "product_faithful_system_prompt": True,
            "runtime": "FreeToken OpenAI-compatible streaming API",
            "runtime_compatibility_fallback": "FREETOKEN_FORCE_E4M3_EMU=1 on RTX 4090",
            "generation": {
                "temperature": args.temperature,
                "top_p": args.top_p,
                "top_k": args.top_k,
                "max_seq_len": 4096,
                "max_tokens": args.max_tokens,
                "thinking": False,
                "thinking_controls": {
                    "chat_template_kwargs.enable_thinking": False,
                    "chat_template_kwargs.preserve_thinking": False,
                    "thinking.type": "disabled",
                    "server_reasoning_parser": "off",
                },
                "seed_supported_by_runtime": False,
                "repeat_penalty_supported_by_runtime": False,
            },
            "hard_gate": {
                "probe_turns": 10,
                "generation_failures_required": 0,
                "max_output_contract_violations": 1,
                "critical_state_turns": [3, 4, 6, 7, 9, 10],
                "minimum_correct_critical_state_turns": 5,
                "gpu_peak_limit_mib": 12288,
                "target_mean_first_visible_ms": 3000,
                "quality_decision": "manual semantic review; never inferred from keywords",
            },
            "limitations": [
                "The model retains its own generated history after turn 1.",
                "Whole-machine RAM and GPU samples may include background processes.",
                "FreeToken does not expose a seed or repeat-penalty control on this API surface.",
                "One frozen trajectory is a regression gate, not a population accuracy estimate.",
            ],
        },
        "arm": {
            "key": "qwen36_35b_a3b_nvfp4_freetoken",
            "label": "Qwen3.6-35B-A3B-NVFP4 裸基座（FreeToken混合卸载，关闭思考）",
            "model": args.model,
            "surface": "FreeToken native checkpoint and chat template",
            "turns": turns,
            "resources": sampler.summary(),
        },
        "turns": report_turns,
        "summary": {
            "turns": len(turns),
            "speed": _speed_summary(turns),
            "resources": sampler.summary(),
            "automatic_quality_judgment": False,
        },
    }
    report_path = output_dir / "probe.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"PROBE={report_path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Qwen3.6-35B-A3B NVFP4 through FreeToken on the frozen hard gate"
    )
    parser.add_argument("output_dir")
    parser.add_argument("--base-url", default="http://127.0.0.1:1919")
    parser.add_argument("--model", default="Qwen3.6-35B-A3B-NVFP4")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=180)
    parser.add_argument("--temperature", type=float, default=0.75)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        raise ValueError("--limit must be between 1 and 10 for the hard gate")
    if args.max_tokens < 1 or args.temperature < 0 or args.top_k < 1:
        raise ValueError("generation controls are invalid")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
