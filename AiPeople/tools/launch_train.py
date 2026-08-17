# -*- coding: utf-8 -*-
"""带资源限制的训练启动器：设置 CPU 亲和性（32 逻辑核 → 19 核 ≈ 59% ≤ 60%）后
执行训练命令。GPU 上限靠训练配置与 monitor_training.py 监控兜底。

用法:
  python tools/launch_train.py --cwd <训练包目录> -- train <args...>
  例: python tools/launch_train.py --cwd .../training_package_baiweixi_qwen35_4b -- llamafactory-cli train configs/baiweixi_4b_8g.yaml
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import psutil

CPU_CORES = 32
ALLOWED_CORES = 19  # 19/32 ≈ 59.4%


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True, help="训练包目录（含 configs/data）")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="要执行的训练命令")
    args = ap.parse_args()

    if not args.cmd or args.cmd[0] == "--":
        args.cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not args.cmd:
        print("错误：缺少训练命令", file=sys.stderr)
        return 2

    cwd = Path(args.cwd)
    if not cwd.is_dir():
        print("错误：cwd 不存在: %s" % cwd, file=sys.stderr)
        return 2

    allowed = list(range(ALLOWED_CORES))
    print("启动: cwd=%s 命令=%s 亲和性=核0-%d（%d/%d ≈ %.1f%%）"
          % (cwd, " ".join(args.cmd), ALLOWED_CORES - 1, ALLOWED_CORES, CPU_CORES,
             ALLOWED_CORES * 100.0 / CPU_CORES), flush=True)

    proc = subprocess.Popen(args.cmd, cwd=str(cwd))
    # 等进程创建完成再设置亲和性
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            p = psutil.Process(proc.pid)
            p.cpu_affinity(allowed)
            print("CPU 亲和性已设置: PID %d → 核 0-%d" % (proc.pid, ALLOWED_CORES - 1), flush=True)
            break
        except psutil.NoSuchProcess:
            break
        except Exception as e:
            time.sleep(0.2)
    else:
        print("警告：设置 CPU 亲和性失败: %s" % e, file=sys.stderr)

    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
