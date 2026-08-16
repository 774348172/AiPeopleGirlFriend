from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.adapters import GenerationOptions
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost


async def main(args: argparse.Namespace) -> int:
    config = Sys12ReleaseConfig.load(args.manifest)
    host = Sys12ReleaseHost(config)
    samples: list[float] = []
    stop = asyncio.Event()
    sampler = asyncio.create_task(_sample_gpu(samples, stop))
    process = None
    try:
        await host.start()
        await host.backend.complete_chat(
            request_id="sys12:combined:warm",
            messages=(
                {"role": "system", "content": "你是白未晞。不要输出思考过程。"},
                {"role": "user", "content": "只回答：好"},
            ),
            options=GenerationOptions(8, 0.1, 0.8, 1.05, seed=6112026),
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(ROOT / "tools" / "run_sys09_retrieval_acceptance.py"),
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                "combined retrieval probe failed: "
                + stderr.decode("utf-8", errors="replace")[-2000:]
            )
    finally:
        stop.set()
        await sampler
        await host.close()
    retrieval_report = json.loads(
        (ROOT / "eval" / "world_mind_p0" / "sys09_retrieval_report.json").read_text(encoding="utf-8")
    )
    report = {
        "schema_version": 1,
        "scope": "sys12_combined_release_resource_probe",
        "generated_at": datetime.now().astimezone().isoformat(),
        "release_path": config.release_path,
        "model_sha256": config.model_sha256,
        "combined_gpu_peak_mib": max(samples, default=0),
        "gpu_samples": samples,
        "retrieval": {
            "profile": retrieval_report["assets"]["reranker"]["deployment_profile"],
            "p95_ms": retrieval_report["recall_ms"]["p95"],
            "passed": retrieval_report["decision"]["sys09_gate"] == "passed",
        },
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **report}, ensure_ascii=False, indent=2))
    return 0


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Measure SYS-12 combined release GPU peak.")
    parser.add_argument("--manifest", default=str(ROOT / "local_runtime" / "sys12_release_manifest.json"))
    parser.add_argument("--output", default=str(ROOT / "eval" / "world_mind_p0" / "sys12_combined_resource_report.json"))
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(_parser().parse_args())))
