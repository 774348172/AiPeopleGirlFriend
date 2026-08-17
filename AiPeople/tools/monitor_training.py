# -*- coding: utf-8 -*-
"""训练资源监控：每 10 秒采样 CPU 总占用与 GPU util/显存，超限写告警。

红线：CPU ≤ 60%、GPU ≤ 95%。持续超限会在日志出现 ALERT 行。
用法:
  python tools/monitor_training.py --log monitor.log [--interval 10]
"""
from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
import time

import psutil

CPU_LIMIT = 60.0
GPU_LIMIT = 95.0


def sample_gpu() -> tuple[float | None, float | None]:
    """返回 (util%, mem_used_MiB)。nvidia-smi 失败返回 (None, None)。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        util, mem = out.split(",")
        return float(util.strip()), float(mem.strip())
    except Exception:
        return None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="monitor.log", help="监控日志输出路径")
    ap.add_argument("--interval", type=float, default=10.0, help="采样间隔秒")
    args = ap.parse_args()

    logf = open(args.log, "a", encoding="utf-8")
    def log(msg: str) -> None:
        line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    log("监控启动：CPU≤%g%% GPU≤%g%%（间隔 %gs）" % (CPU_LIMIT, GPU_LIMIT, args.interval))
    cpu_alerts = gpu_alerts = samples = 0
    cpu_max = gpu_max = 0.0
    gpu_mem_max = 0
    try:
        while True:
            cpu = psutil.cpu_percent(interval=1)
            gpu, mem = sample_gpu()
            samples += 1
            cpu_max = max(cpu_max, cpu)
            if gpu is not None:
                gpu_max = max(gpu_max, gpu)
                gpu_mem_max = max(gpu_mem_max, mem)
            flags = []
            if cpu > CPU_LIMIT:
                cpu_alerts += 1
                flags.append("ALERT CPU %.1f%%" % cpu)
            if gpu is not None and gpu > GPU_LIMIT:
                gpu_alerts += 1
                flags.append("ALERT GPU %.1f%%" % gpu)
            if flags or samples % 6 == 0:  # 超限或每 1 分钟
                suffix = (" 显存 %dMiB" % mem) if gpu is not None else "  nvidia-smi 不可用"
                log("CPU %.1f%% | GPU %s%%%s%s" % (
                    cpu, "%.1f" % gpu if gpu is not None else "N/A", suffix,
                    "  <== " + " / ".join(flags) if flags else ""))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        log("监控结束：采样 %d 次 | CPU 峰值 %.1f%%（超限 %d 次）| GPU 峰值 %.1f%%（超限 %d 次）| 显存峰值 %dMiB"
            % (samples, cpu_max, cpu_alerts, gpu_max, gpu_alerts, gpu_mem_max))
        logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
