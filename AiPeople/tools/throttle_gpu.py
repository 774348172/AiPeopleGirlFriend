# -*- coding: utf-8 -*-
"""GPU 节流器：监控 GPU util，超限时短暂挂起训练进程，把平均占用压到红线内。

红线：GPU ≤ 95%（用户约定）。实现：每 interval 秒采样一次 util，若 > limit 且
距上次挂起 ≥ period 秒，则挂起训练进程 pause 秒再恢复。满负荷时平均 util ≈
limit × (1 - pause/period)（例：period 60 / pause 4 → 平均 ≈ 89%）。

用法:
  python tools/throttle_gpu.py --pid 1497496 [--limit 95 --period 60 --pause 4 --interval 5]
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import subprocess
import sys
import time

from ctypes import wintypes

PROCESS_SUSPEND_RESUME = 0x0800
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def gpu_util() -> float | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return float(out)
    except Exception:
        return None


def suspend(pid: int) -> bool:
    h = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h:
        return False
    try:
        return ntdll.NtSuspendProcess(h) == 0
    finally:
        kernel32.CloseHandle(h)


def resume(pid: int) -> bool:
    h = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h:
        return False
    try:
        return ntdll.NtResumeProcess(h) == 0
    finally:
        kernel32.CloseHandle(h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True, help="训练进程 PID")
    ap.add_argument("--limit", type=float, default=95.0, help="GPU util 红线")
    ap.add_argument("--period", type=float, default=60.0, help="两次挂起最小间隔（秒）")
    ap.add_argument("--pause", type=float, default=4.0, help="每次挂起时长（秒）")
    ap.add_argument("--interval", type=float, default=5.0, help="采样间隔（秒）")
    args = ap.parse_args()

    def log(msg: str) -> None:
        print("[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)

    log("节流启动：PID=%d 红线=%g%% 周期=%gs 挂起=%gs" % (
        args.pid, args.limit, args.period, args.pause))
    last_pause = 0.0
    paused = False
    while True:
        try:
            u = gpu_util()
        except KeyboardInterrupt:
            break
        if u is None:
            time.sleep(args.interval)
            continue
        now = time.time()
        if u > args.limit and now - last_pause >= args.period and not paused:
            log("util %.0f%% > %g%% → 挂起 %gs" % (u, args.limit, args.pause))
            if suspend(args.pid):
                paused = True
                time.sleep(args.pause)
                resume(args.pid)
                paused = False
                last_pause = now
                log("恢复")
            else:
                log("挂起失败（进程可能已退出）")
                time.sleep(args.interval)
        else:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        pass
