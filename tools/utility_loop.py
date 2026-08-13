# -*- coding: utf-8 -*-
"""训练后效用闭环编排（阶段 5 E-5）。

分步执行（每步可独立运行；训练在外部 Mac M3 MLX 执行，本机只做数据/评测/判定）：

  1. 数据发布   --step publish   → 阶段 4 发布产物（含 split anchors 训练数据）
  2. 训练       --step train     → 打印 freeze03 契约执行指令（Mac 上跑）
  3. 评测       --step eval      → eval_delta（误拒率/paired delta/seed 聚合）
  4. 晋升判定   --step promote   → PromotionGate 7 条（promotion report）
  5. 失败回流   --step gap       → behavior_gap 登记（不产出关键词规则）

用法:
  python tools/utility_loop.py --step publish --metadata ... --data ... --ledger ... --out ...
  python tools/utility_loop.py --step train
  python tools/utility_loop.py --step eval --pairs ... --cases ...
  python tools/utility_loop.py --step promote --evidence promotion_evidence.json
  python tools/utility_loop.py --step gap --ledger ... --registry ...
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FREEZE03 = ROOT / "设计文档" / "通用数据生成器" / "施工" / "freeze03_contract.json"


def step_publish(args) -> int:
    cmd = [
        sys.executable, str(ROOT / "tools" / "publish_dataset.py"),
        "--metadata", args.metadata, "--data", args.data,
        "--ledger", args.ledger, "--out", args.out,
        "--dataset-id", args.dataset_id or "qinweixi-v4",
    ]
    if args.blocklist:
        cmd += ["--blocklist", args.blocklist]
    print(f"[step 1] 数据发布: {' '.join(cmd)}")
    return subprocess.call(cmd)


def step_train(args) -> int:
    contract = json.loads(FREEZE03.read_text(encoding="utf-8"))
    print("[step 2] 训练（外部 Mac M3 MLX 执行，本机无训练能力）")
    print(f"  契约: {FREEZE03.name}")
    print(f"  基座: {contract['base_model']['model_id']}")
    print(f"  LoRA: {contract['lora_config']}")
    print(f"  seed_set: {contract['seed_set']}（晋升条件 4 需 ≥3 seed 独立训练+评测）")
    print("  执行: 在 Mac 上跑 training_package_m3_qwen35/02_train.sh（每 seed 一次）")
    print("  前置: 训练数据必须来自 step 1 发布产物（含 split anchors），非 01 脚本随机切分")
    return 0


def step_eval(args) -> int:
    cmd = [sys.executable, str(ROOT / "tools" / "eval_delta.py")]
    if args.pairs:
        cmd += ["--pairs", args.pairs]
    if args.cases:
        cmd += ["--cases", args.cases]
    if args.seed_deltas:
        cmd += ["--seed-deltas", args.seed_deltas]
    print(f"[step 3] 评测 delta: {' '.join(cmd)}")
    return subprocess.call(cmd)


def step_promote(args) -> int:
    cmd = [
        sys.executable, str(ROOT / "tools" / "promotion_check.py"),
        "--evidence", args.evidence,
    ]
    print(f"[step 4] 晋升判定（7 条）: {' '.join(cmd)}")
    return subprocess.call(cmd)


def step_gap(args) -> int:
    cmd = [
        sys.executable, str(ROOT / "tools" / "register_behavior_gap.py"),
        "--ledger", args.ledger, "--registry", args.registry,
    ]
    print(f"[step 5] 失败回流（behavior_gap 登记，不产出关键词规则）: {' '.join(cmd)}")
    return subprocess.call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--step", required=True,
                    choices=["publish", "train", "eval", "promote", "gap"])
    ap.add_argument("--metadata")
    ap.add_argument("--data")
    ap.add_argument("--ledger")
    ap.add_argument("--out")
    ap.add_argument("--dataset-id")
    ap.add_argument("--blocklist")
    ap.add_argument("--pairs")
    ap.add_argument("--cases")
    ap.add_argument("--seed-deltas")
    ap.add_argument("--evidence")
    ap.add_argument("--registry")
    args = ap.parse_args()
    steps = {
        "publish": step_publish,
        "train": step_train,
        "eval": step_eval,
        "promote": step_promote,
        "gap": step_gap,
    }
    return steps[args.step](args)


if __name__ == "__main__":
    sys.exit(main())
