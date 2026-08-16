from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.adapters import GenerationOptions
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost


MESSAGES = (
    {"role": "system", "content": "你是本地发布链路测试模型。不要输出思考过程。"},
    {"role": "user", "content": "持续输出简短中文句子，直到系统截断。"},
)


async def main() -> int:
    config = Sys12ReleaseConfig.load(ROOT / "local_runtime" / "sys12_release_manifest.json")
    priority = await _priority_probe(config)
    recovery = await _recovery_probe(config)
    lifecycle = await _lifecycle_probe(config)
    report = {
        "schema_version": 1,
        "scope": "sys12_release_resilience",
        "generated_at": datetime.now().astimezone().isoformat(),
        "asset_sha256": config.model_sha256,
        "priority": priority,
        "recovery": recovery,
        "lifecycle": lifecycle,
        "decision": {
            "foreground_priority_passed": (
                priority["background_cancelled"]
                and priority["background_requeued_completed"]
                and priority["foreground_ms"] < config.reply_80_tokens_p95_ms_max
                and priority["gate_metrics"]["foreground_preemptions"] >= 1
            ),
            "crash_recovery_passed": recovery["passed"],
            "repeatable_lifecycle_passed": lifecycle["passed"],
        },
    }
    report["decision"]["passed"] = all(report["decision"].values())
    output = ROOT / "eval" / "world_mind_p0" / "sys12_release_resilience_report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **report}, ensure_ascii=False, indent=2))
    return 0 if report["decision"]["passed"] else 1


async def _priority_probe(config: Sys12ReleaseConfig) -> dict[str, object]:
    host = Sys12ReleaseHost(config)
    cancelled = False
    try:
        await host.start()
        background = asyncio.create_task(
            host.backend.complete_chat(
                request_id="sys12:MEMORY_PROPOSE:background",
                messages=MESSAGES,
                options=GenerationOptions(600, 0.2, 0.8, 1.05, seed=6112026),
            )
        )
        await asyncio.sleep(0.25)
        started = time.perf_counter()
        foreground = await host.backend.complete_chat(
            request_id="sys12:foreground",
            messages=(MESSAGES[0], {"role": "user", "content": "只回答：前台已响应"}),
            options=GenerationOptions(16, 0.1, 0.8, 1.05, seed=6112026),
        )
        foreground_ms = (time.perf_counter() - started) * 1000
        try:
            await background
        except asyncio.CancelledError:
            cancelled = True
        requeued = await host.backend.complete_chat(
            request_id="sys12:MEMORY_PROPOSE:requeued",
            messages=(MESSAGES[0], {"role": "user", "content": "只回答：后台已恢复"}),
            options=GenerationOptions(16, 0.1, 0.8, 1.05, seed=6112026),
        )
        return {
            "background_cancelled": cancelled,
            "background_requeued_completed": bool(requeued.strip()),
            "foreground_ms": foreground_ms,
            "foreground_output": foreground,
            "gate_metrics": host.gate.snapshot(),
        }
    finally:
        await host.close()


async def _recovery_probe(config: Sys12ReleaseConfig) -> dict[str, object]:
    host = Sys12ReleaseHost(config)
    try:
        await host.start()
        before = await _short_call(host, "before-crash", "只回答：恢复前")
        pid = host.raw_backend.process_id
        if pid is None:
            raise RuntimeError("release server PID unavailable")
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        await asyncio.sleep(0.5)
        started = time.perf_counter()
        after = await _short_call(host, "after-crash", "只回答：恢复后")
        recovery_ms = (time.perf_counter() - started) * 1000
        new_pid = host.raw_backend.process_id
        return {
            "passed": bool(before.strip()) and bool(after.strip()) and new_pid not in (None, pid),
            "old_pid": pid,
            "new_pid": new_pid,
            "recovery_ms": recovery_ms,
            "before_output": before,
            "after_output": after,
        }
    finally:
        await host.close()


async def _lifecycle_probe(config: Sys12ReleaseConfig) -> dict[str, object]:
    cycles = []
    for index in range(3):
        host = Sys12ReleaseHost(config)
        try:
            await host.start()
            output = await _short_call(host, f"cycle-{index}", f"只回答：周期{index}")
            cycles.append({"cycle": index, "startup_ms": host.metrics().startup_ms, "output": output})
        finally:
            await host.close()
    return {"passed": len(cycles) == 3 and all(item["output"].strip() for item in cycles), "cycles": cycles}


async def _short_call(host: Sys12ReleaseHost, request_id: str, prompt: str) -> str:
    return await host.backend.complete_chat(
        request_id=f"sys12:{request_id}",
        messages=(MESSAGES[0], {"role": "user", "content": prompt}),
        options=GenerationOptions(16, 0.1, 0.8, 1.05, seed=6112026),
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
