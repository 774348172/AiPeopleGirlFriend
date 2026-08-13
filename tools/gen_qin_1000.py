# -*- coding: utf-8 -*-
"""批量生成 1000 条新训练数据（T15，2026-08-07）。

前置：训练验收通过后运行（用户指令：训练效果正确 → 直接生成 1000 条）。
生成器已含 T14 全部修复（canon 澄清/facts 标签/禁基地表述/玩家视角约束/池条目修正）。

配额（矩阵 6 池已满不重复，未满池按 recipe 比例裁剪到 1000）：
  casual 480 / romance 307 / emotion 177 / identity 12 / protective 24
分批：11 批（每批 ≤150，防 API 降速），输出 qin_v4_1000_part<N>.*，最后合并。

用法: python tools/gen_qin_1000.py            （需 OPENAI_API_KEY 环境变量）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "训练数据"
GEN = ROOT / "gen_qin_v4.py"

# (批名, --range-types 参数, 该批条数)——注意用完整 task_type（pick_range 不支持简写）
BATCHES = [
    ("p01", "reply_casual=0:150", 150),
    ("p02", "reply_casual=150:300", 150),
    ("p03", "reply_casual=300:450", 150),
    ("p04", "reply_casual=450:480", 30),
    ("p05", "reply_romance=0:150", 150),
    ("p06", "reply_romance=150:300", 150),
    ("p07", "reply_romance=300:307", 7),
    ("p08", "reply_emotion=0:150", 150),
    ("p09", "reply_emotion=150:177", 27),
    ("p10", "reply_identity=0:12", 12),
    ("p11", "reply_protective=0:24", 24),
]


def run_batch(name: str, spec: str, count: int) -> int:
    out = DATA_DIR / f"qin_v4_1000_{name}.jsonl"
    cmd = [
        sys.executable, str(GEN),
        "--count", str(count),
        "--range-types", spec,
        "--out", str(out),
    ]
    print(f"\n=== {name}: {spec} ({count} 条) ===", flush=True)
    env = dict(os.environ)
    env.pop("HF_HUB_OFFLINE", None)  # 生成走 API，不需离线
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8")
    tail = proc.stdout.strip().splitlines()
    for line in tail[-6:]:
        print("  ", line, flush=True)
    if proc.returncode != 0:
        print(f"  [失败] {name} exit={proc.returncode}", flush=True)
        print(proc.stderr[-800:], flush=True)
        return 0
    # 统计导出条数
    n = 0
    if out.exists():
        with open(out, encoding="utf-8") as f:
            n = sum(1 for _ in f)
    print(f"  → {name} 导出 {n} 条", flush=True)
    return n


def merge(parts: list[tuple[str, int]]) -> None:
    rows, metas, gates = [], [], []
    for name, _ in parts:
        for suffix, target in ((".jsonl", rows), (".metadata.jsonl", metas), (".gate_report.jsonl", gates)):
            p = DATA_DIR / f"qin_v4_1000_{name}{suffix}"
            if p.exists():
                with open(p, encoding="utf-8") as f:
                    target.extend(f.readlines())
    merged = DATA_DIR / "qin_v4_1000.jsonl"
    with open(merged, "w", encoding="utf-8") as f:
        f.writelines(rows)
    with open(DATA_DIR / "qin_v4_1000.metadata.jsonl", "w", encoding="utf-8") as f:
        f.writelines(metas)
    print(f"\n合并完成: {merged} ({len(rows)} 条) | metadata {len(metas)} | gate {len(gates)}")


def main() -> None:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        print("[error] 未配置 OPENAI_API_KEY（从 .env 导入）")
        sys.exit(1)
    total = 0
    done: list[tuple[str, int]] = []
    for name, spec, count in BATCHES:
        n = run_batch(name, spec, count)
        done.append((name, n))
        total += n
    print(f"\n===== 各批导出: {done} | 合计 {total}/1000 =====")
    merge(done)


if __name__ == "__main__":
    main()
