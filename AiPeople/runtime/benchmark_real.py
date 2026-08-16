from __future__ import annotations

import argparse
import asyncio
import json
import math
import platform
import statistics
import subprocess
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import Completed, Failed, RelationshipRuntime, RuntimeConfig, UserMessage
from .adapters import GenerationStats, LlamaCppConfig, LlamaCppReplyModel


PROMPTS = {
    80: "这是性能测试。只输出从1到500的阿拉伯数字，每个数字用逗号分隔，按顺序持续输出，禁止解释、省略或提前结束。",
    160: "这是性能测试。只输出从1到500的阿拉伯数字，每个数字用逗号分隔，按顺序持续输出，禁止解释、省略或提前结束。",
}
STABILITY_PROMPT = "用一句自然的中文告诉我你现在还在。"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the real local model")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("local_runtime/model_runtime_manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("local_runtime/stage4_p0_16_results.json"),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("local_runtime/stage4_benchmark_data"),
    )
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--discard", type=int, default=5)
    parser.add_argument("--stability-minutes", type=float, default=60.0)
    parser.add_argument("--stability-interval-seconds", type=float, default=60.0)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.samples < 2 or args.discard < 0:
        parser.error("samples must be at least 2 and discard cannot be negative")
    if args.stability_minutes <= 0 or args.stability_interval_seconds <= 0:
        parser.error("stability duration and interval must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    base = LlamaCppConfig.from_manifest(
        args.config,
        port=args.port,
        seed=42,
        collect_usage=True,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": datetime.now().astimezone().isoformat(),
        "environment": _environment(),
        "baseline_resources": _sample_resources(None),
        "manifest": str(Path(args.config).resolve()),
        "model_sha256": _manifest_model_hash(args.config),
        "launch": {
            "context_size": base.context_size,
            "parallel": base.parallel,
            "gpu_layers": base.gpu_layers,
            "flash_attention": base.flash_attention,
            "kv_cache_k": base.kv_cache_k,
            "kv_cache_v": base.kv_cache_v,
            "seed": 42,
        },
        "cold_start_seconds": None,
        "performance_samples": [],
        "stability_samples": [],
        "status": "running",
    }
    _write_json(args.output, report)

    per_bucket = math.ceil(args.samples / 2)
    total_kept = 0
    for target_tokens in (80, 160):
        if total_kept >= args.samples:
            break
        bucket_count = min(per_bucket, args.samples - total_kept)
        config = replace(base, max_tokens=target_tokens)
        model = LlamaCppReplyModel(config)
        bucket_pid: int | None = None
        runtime_config = RuntimeConfig(
            args.data_dir,
            database_name=f"benchmark_{target_tokens}.sqlite3",
        )
        start_time = time.perf_counter()
        print(f"loading model for {target_tokens}-token bucket", flush=True)
        async with RelationshipRuntime.open(runtime_config, model) as runtime:
            bucket_pid = model.process_id
            startup_seconds = time.perf_counter() - start_time
            if report["cold_start_seconds"] is None:
                report["cold_start_seconds"] = startup_seconds
                report["loaded_resources"] = _sample_resources(model.process_id)
            print(f"model ready in {startup_seconds:.3f}s", flush=True)

            for index in range(args.discard):
                await _run_turn(
                    runtime,
                    f"discard-{target_tokens}-{index}-{uuid.uuid4()}",
                    PROMPTS[target_tokens],
                )

            for index in range(bucket_count):
                completed = await _run_turn(
                    runtime,
                    f"perf-{target_tokens}-{index}-{uuid.uuid4()}",
                    PROMPTS[target_tokens],
                )
                sample = _performance_sample(
                    target_tokens, completed, model.last_generation_stats
                )
                report["performance_samples"].append(sample)
                total_kept += 1
                _write_json(args.output, report)
                print(
                    f"performance {total_kept}/{args.samples}: "
                    f"target={target_tokens} generated={sample['generated_tokens']} "
                    f"first={sample['first_delta_ms']:.1f}ms "
                    f"total={sample['model_total_ms']:.1f}ms",
                    flush=True,
                )

            if target_tokens == 160:
                await _run_stability(
                    runtime,
                    model,
                    report,
                    args.output,
                    args.stability_minutes * 60.0,
                    args.stability_interval_seconds,
                )

        report.setdefault("process_shutdown", []).append(
            {
                "pid": bucket_pid,
                "exited_after_close": bucket_pid is not None
                and not _process_exists(bucket_pid),
            }
        )

    report["completed_at"] = datetime.now().astimezone().isoformat()
    report["summary"] = _summarize(report)
    report["status"] = "completed"
    _write_json(args.output, report)
    markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(f"results: {args.output.resolve()}", flush=True)
    print(f"report: {markdown_path.resolve()}", flush=True)


async def _run_turn(
    runtime: RelationshipRuntime, request_id: str, prompt: str
) -> Completed:
    message = UserMessage(
        request_id=request_id,
        conversation_id="stage4-benchmark",
        text=prompt,
        occurred_at=datetime.now(ZoneInfo("Asia/Shanghai")),
        timezone="Asia/Shanghai",
    )
    completed: Completed | None = None
    async for event in runtime.handle_turn(message):
        if isinstance(event, Failed):
            raise RuntimeError(f"benchmark turn failed with {event.code}")
        if isinstance(event, Completed):
            completed = event
    if completed is None:
        raise RuntimeError("benchmark turn did not complete")
    return completed


async def _run_stability(
    runtime: RelationshipRuntime,
    model: LlamaCppReplyModel,
    report: dict[str, Any],
    output: Path,
    duration_seconds: float,
    interval_seconds: float,
) -> None:
    started = time.monotonic()
    count = math.floor(duration_seconds / interval_seconds) + 1
    for index in range(count):
        target = started + index * interval_seconds
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        completed = await _run_turn(
            runtime,
            f"stability-{index}-{uuid.uuid4()}",
            STABILITY_PROMPT,
        )
        sample = {
            "elapsed_seconds": time.monotonic() - started,
            "model_total_ms": completed.metrics.model_total_ms,
            **_sample_resources(model.process_id),
        }
        report["stability_samples"].append(sample)
        _write_json(output, report)
        print(
            f"stability {index + 1}/{count}: "
            f"elapsed={sample['elapsed_seconds']:.1f}s "
            f"rss={sample.get('working_set_mib')}MiB "
            f"gpu_total={sample.get('system_gpu_mib')}MiB",
            flush=True,
        )


def _performance_sample(
    target_tokens: int,
    completed: Completed,
    stats: GenerationStats | None,
) -> dict[str, Any]:
    return {
        "target_tokens": target_tokens,
        "prompt_tokens": stats.prompt_tokens if stats else None,
        "generated_tokens": stats.generated_tokens if stats else None,
        "prompt_tokens_per_second": stats.prompt_tokens_per_second if stats else None,
        "generated_tokens_per_second": stats.generated_tokens_per_second if stats else None,
        "first_delta_ms": completed.metrics.model_first_delta_ms,
        "model_total_ms": completed.metrics.model_total_ms,
        "total_ms": completed.metrics.total_ms,
        "output_chars": completed.metrics.output_chars,
        "delta_count": completed.metrics.delta_count,
    }


def _summarize(report: dict[str, Any]) -> dict[str, Any]:
    performance: list[dict[str, Any]] = report["performance_samples"]
    bucket_summaries: dict[str, Any] = {}
    for target in (80, 160):
        valid = [
            item
            for item in performance
            if item["target_tokens"] == target
            and isinstance(item.get("generated_tokens"), int)
            and item["generated_tokens"] >= target
        ]
        bucket_summaries[str(target)] = {
            "valid_samples": len(valid),
            "first_delta_p95_ms": _p95(
                [item["first_delta_ms"] for item in valid if item["first_delta_ms"] is not None]
            ),
            "model_total_p95_ms": _p95([item["model_total_ms"] for item in valid]),
            "generated_tokens_per_second_p50": _median(
                [
                    item["generated_tokens_per_second"]
                    for item in valid
                    if item["generated_tokens_per_second"] is not None
                ]
            ),
        }
    stability: list[dict[str, Any]] = report["stability_samples"]
    gpu = [item["process_gpu_mib"] for item in stability if item.get("process_gpu_mib") is not None]
    system_gpu = [item["system_gpu_mib"] for item in stability if item.get("system_gpu_mib") is not None]
    rss = [item["working_set_mib"] for item in stability if item.get("working_set_mib") is not None]
    handles = [item["handle_count"] for item in stability if item.get("handle_count") is not None]
    stability_duration = stability[-1]["elapsed_seconds"] if stability else 0.0
    baseline_gpu = report.get("baseline_resources", {}).get("system_gpu_mib")
    peak_system_gpu = max(system_gpu, default=None)
    peak_gpu_delta = (
        peak_system_gpu - baseline_gpu
        if peak_system_gpu is not None and baseline_gpu is not None
        else None
    )
    summary = {
        "buckets": bucket_summaries,
        "peak_process_gpu_mib": max(gpu, default=None),
        "peak_system_gpu_mib": peak_system_gpu,
        "peak_gpu_delta_mib": peak_gpu_delta,
        "peak_working_set_mib": max(rss, default=None),
        "peak_handle_count": max(handles, default=None),
        "stability_duration_seconds": stability_duration,
        "sustained_gpu_growth": _sustained_growth(system_gpu, 64.0),
        "sustained_rss_growth": _sustained_growth(rss, 64.0),
        "sustained_handle_growth": _sustained_growth(handles, 32.0),
    }
    first_p95_values = [
        bucket["first_delta_p95_ms"]
        for bucket in bucket_summaries.values()
        if bucket["first_delta_p95_ms"] is not None
    ]
    summary["acceptance"] = {
        "sample_count": len(performance) >= 30,
        "first_delta_p95_under_2s": bool(first_p95_values)
        and max(first_p95_values) < 2000,
        "80_tokens_p95_under_5s": _under(
            bucket_summaries["80"]["model_total_p95_ms"], 5000
        ),
        "160_tokens_p95_under_8s": _under(
            bucket_summaries["160"]["model_total_p95_ms"], 8000
        ),
        "peak_gpu_delta_under_3_5gib": peak_gpu_delta is not None
        and peak_gpu_delta < 3584,
        "one_hour_completed": stability_duration >= 3600,
        "no_sustained_resource_growth": not any(
            (
                summary["sustained_gpu_growth"],
                summary["sustained_rss_growth"],
                summary["sustained_handle_growth"],
            )
        ),
    }
    return summary


def _sustained_growth(values: list[float | int], tolerance: float) -> bool:
    if len(values) < 8:
        return False
    quarter = max(2, len(values) // 4)
    start = statistics.median(values[:quarter])
    end = statistics.median(values[-quarter:])
    return end - start > tolerance


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _under(value: float | None, limit: float) -> bool:
    return value is not None and value < limit


def _sample_resources(pid: int | None) -> dict[str, Any]:
    if pid is None:
        return {
            "pid": None,
            "working_set_mib": None,
            "handle_count": None,
            "process_gpu_mib": None,
            "system_gpu_mib": _system_gpu_used_mib(),
        }
    process = _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"$p=Get-Process -Id {pid} -ErrorAction Stop; \"$($p.WorkingSet64),$($p.HandleCount)\"",
        ]
    )
    working_set_mib: float | None = None
    handle_count: int | None = None
    if process:
        try:
            working_set, handles = process.split(",", 1)
            working_set_mib = int(working_set) / 1024 / 1024
            handle_count = int(handles)
        except ValueError:
            pass
    gpu_mib: float | None = None
    gpu_rows = _run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    for row in gpu_rows.splitlines() if gpu_rows else ():
        try:
            row_pid, memory = (part.strip() for part in row.split(",", 1))
            if int(row_pid) == pid:
                gpu_mib = float(memory)
                break
        except ValueError:
            continue
    return {
        "pid": pid,
        "working_set_mib": working_set_mib,
        "handle_count": handle_count,
        "process_gpu_mib": gpu_mib,
        "system_gpu_mib": _system_gpu_used_mib(),
    }


def _system_gpu_used_mib() -> float | None:
    output = _run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ]
    )
    if not output:
        return None
    try:
        return sum(float(line.strip()) for line in output.splitlines())
    except ValueError:
        return None


def _process_exists(pid: int) -> bool:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"Get-Process -Id {pid} -ErrorAction SilentlyContinue",
        ],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _environment() -> dict[str, Any]:
    gpu = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    power = _run(["powercfg", "/getactivescheme"])
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "gpu": gpu,
        "power_scheme": power,
    }


def _manifest_model_hash(path: Path) -> str | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))["model"]["sha256"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _run(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def _write_json(path: Path, report: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    acceptance = summary["acceptance"]
    rows = []
    for target in ("80", "160"):
        bucket = summary["buckets"][target]
        rows.append(
            f"| {target} | {bucket['valid_samples']} | "
            f"{_format(bucket['first_delta_p95_ms'])} | "
            f"{_format(bucket['model_total_p95_ms'])} | "
            f"{_format(bucket['generated_tokens_per_second_p50'])} |"
        )
    checks = "\n".join(
        f"- [{'x' if passed else ' '}] `{name}`" for name, passed in acceptance.items()
    )
    return f"""# 阶段 4 P0-16 性能与稳定性报告

> 开始：{report['started_at']}  
> 完成：{report['completed_at']}  
> 模型 SHA-256：`{report['model_sha256']}`

## 性能

| 目标 token | 有效样本 | 首字 P95 ms | 完成 P95 ms | 生成速度 P50 token/s |
|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

- 冷启动及预热：{_format(report['cold_start_seconds'])} 秒
- 峰值模型进程显存：{_format(summary['peak_process_gpu_mib'])} MiB
- 峰值整卡显存：{_format(summary['peak_system_gpu_mib'])} MiB
- 相对启动前峰值显存增量：{_format(summary['peak_gpu_delta_mib'])} MiB
- 峰值工作集：{_format(summary['peak_working_set_mib'])} MiB
- 峰值句柄：{_format(summary['peak_handle_count'])}
- 稳定性时长：{_format(summary['stability_duration_seconds'])} 秒

## 验收

{checks}

原始逐轮样本保存在同名 JSON 文件中。报告不保存 prompt 或模型回复正文。
"""


def _format(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


if __name__ == "__main__":
    asyncio.run(main())
