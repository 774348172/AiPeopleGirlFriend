from __future__ import annotations

import argparse
import asyncio
import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from runtime.adapters import GenerationOptions
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost


SYSTEM = "你是白未晞。只完成当前测试指令，不输出思考过程。性能长度测试时必须持续输出到系统截断，不得提前结束。"


async def main(args: argparse.Namespace) -> int:
    config = Sys12ReleaseConfig.load(args.manifest)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    candidates: dict[str, Any] = {}
    for name in args.paths:
        await _unload_ollama()
        await asyncio.sleep(2)
        candidates[name] = await _measure_candidate(config, name, args)
    report = {
        "schema_version": 1,
        "scope": "sys12_same_artifact_inference_comparison",
        "generated_at": datetime.now().astimezone().isoformat(),
        "manifest": str(config.manifest_path),
        "asset_sha256": config.model_sha256,
        "sample_plan": {
            "cold_starts": args.cold_starts,
            "warm_calls": args.warm_calls,
            "reply_80": args.reply_samples,
            "reply_160": args.reply_samples,
            "structured": args.structured_samples,
        },
        "candidates": candidates,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "candidates": candidates}, ensure_ascii=False, indent=2))
    return 0


async def _measure_candidate(config, name: str, args) -> dict[str, Any]:
    cold_starts: list[float] = []
    lifecycle_passed = True
    for _ in range(args.cold_starts):
        host = Sys12ReleaseHost(config, inference_path=name)
        try:
            await host.start()
            cold_starts.append(host.metrics().startup_ms)
        except Exception:
            lifecycle_passed = False
            raise
        finally:
            await host.close()
        if name == "ollama":
            await _unload_ollama()
        await asyncio.sleep(1)
    host = Sys12ReleaseHost(config, inference_path=name)
    gpu_samples: list[float] = []
    stop = asyncio.Event()
    sampler = asyncio.create_task(_sample_gpu(gpu_samples, stop))
    records: dict[str, list[dict[str, Any]]] = {
        "warm": [],
        "reply_80": [],
        "reply_160": [],
        "structured": [],
    }
    try:
        await host.start()
        cold_first_call = await _call(host, "cold-first-call", 16, "用一句很短的话回答：你现在在做什么？")
        for index in range(args.warm_calls):
            records["warm"].append(await _call(host, f"warm-{index}", 16, "用一句很短的话回答：你现在在做什么？"))
        for band, tokens in (("reply_80", 80), ("reply_160", 160)):
            for index in range(args.reply_samples):
                records[band].append(
                    await _call(
                        host,
                        f"{band}-{index}",
                        tokens,
                        "持续重复输出“春雨落在窗边”这句话，不要编号，不要解释，不要使用结束语，直到系统强制截断。",
                    )
                )
        schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "sys12_probe_v1",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["reply", "state"],
                    "properties": {
                        "reply": {"type": "string", "minLength": 1, "maxLength": 300},
                        "state": {"type": "string", "enum": ["ok"]},
                    },
                },
            },
        }
        for index in range(args.structured_samples):
            records["structured"].append(
                await _call(host, f"structured-{index}", 96, "返回符合约束的 JSON，state 必须为 ok。", schema)
            )
    finally:
        stop.set()
        await sampler
        await host.close()
        if name == "ollama":
            await _unload_ollama()
    return {
        "name": name,
        "asset_sha256": config.model_sha256,
        "lifecycle_passed": lifecycle_passed and host.state == "stopped",
        "cold_start_ms": _summary(cold_starts),
        "cold_first_call": cold_first_call,
        "combined_gpu_peak_mib": max(gpu_samples, default=0),
        "warm_first_visible_token_ms": _summary(_metric(records["warm"], "first_visible_ms")),
        "warm_total_ms": _summary(_metric(records["warm"], "total_ms")),
        "reply_80_tokens_ms": _summary(_metric(records["reply_80"], "total_ms")),
        "reply_160_tokens_ms": _summary(_metric(records["reply_160"], "total_ms")),
        "structured_total_ms": _summary(_metric(records["structured"], "total_ms")),
        "structured_success_rate": sum(row["structured_valid"] for row in records["structured"]) / max(1, len(records["structured"])),
        "records": records,
    }


async def _call(host, request_id: str, max_tokens: int, user: str, response_format=None) -> dict[str, Any]:
    started = time.perf_counter()
    text = await host.backend.complete_chat(
        request_id=f"sys12:{request_id}",
        messages=({"role": "system", "content": SYSTEM}, {"role": "user", "content": user}),
        options=GenerationOptions(max_tokens, 0.2, 0.85, 1.05, seed=6112026),
        response_format=response_format,
    )
    total_ms = (time.perf_counter() - started) * 1000
    first_visible_ms = None
    generated_tokens = None
    raw = host.raw_backend
    if host.inference_path == "llama_cpp":
        stats = raw.last_generation_stats
        if stats is not None:
            first_visible_ms = None if stats.first_token_seconds is None else stats.first_token_seconds * 1000
            generated_tokens = stats.generated_tokens
    else:
        metrics = raw.last_generation_metrics
        if metrics is not None:
            first_visible_ms = metrics.first_content_ms
            generated_tokens = metrics.eval_count
    structured_valid = True
    if response_format is not None:
        try:
            value = json.loads(text)
            structured_valid = isinstance(value, dict) and value.get("state") == "ok" and isinstance(value.get("reply"), str)
        except json.JSONDecodeError:
            structured_valid = False
    return {
        "request_id": request_id,
        "first_visible_ms": first_visible_ms,
        "total_ms": total_ms,
        "generated_tokens": generated_tokens,
        "output_chars": len(text),
        "structured_valid": structured_valid,
    }


async def _sample_gpu(samples: list[float], stop: asyncio.Event) -> None:
    while not stop.is_set():
        value = await asyncio.to_thread(_gpu_used_mib)
        if value is not None:
            samples.append(value)
        try:
            await asyncio.wait_for(stop.wait(), 0.1)
        except TimeoutError:
            pass


def _gpu_used_mib() -> float | None:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=5,
    )
    if result.returncode != 0:
        return None
    try:
        return sum(float(line) for line in result.stdout.decode().splitlines() if line.strip())
    except ValueError:
        return None


async def _unload_ollama() -> None:
    await asyncio.to_thread(
        subprocess.run,
        ["ollama", "stop", "baiweixi-7b-q5-k-m:latest"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30,
    )


def _metric(rows: list[dict[str, Any]], key: str) -> list[float]:
    return [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]


def _summary(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None}
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p50": ordered[max(0, math.ceil(len(ordered) * 0.5) - 1)],
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
        "max": ordered[-1],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare SYS-12 inference paths with one frozen artifact.")
    parser.add_argument("--manifest", default=str(ROOT / "local_runtime" / "sys12_release_manifest.json"))
    parser.add_argument("--output", default=str(ROOT / "eval" / "world_mind_p0" / "sys12_inference_comparison.json"))
    parser.add_argument("--paths", nargs="+", choices=("llama_cpp", "ollama"), default=("llama_cpp", "ollama"))
    parser.add_argument("--cold-starts", type=int, default=2)
    parser.add_argument("--warm-calls", type=int, default=10)
    parser.add_argument("--reply-samples", type=int, default=20)
    parser.add_argument("--structured-samples", type=int, default=10)
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(_parser().parse_args())))
