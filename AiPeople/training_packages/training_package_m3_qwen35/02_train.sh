#!/bin/bash
# 02_train.sh — MLX LoRA 训练（Apple M3 系列）
# 基座: Qwen3.5-4B (MLX 自动下载并转换；VLM 架构，纯文本训练)
# 特点: 全精度 LoRA（无需量化训练）、低学习率稳定人格、nothink 模板
#
# 用法:
#   首次训练:   ./02_train.sh
#   称呼纠错续训: RESUME=1 ./02_train.sh
#     （在已有 adapters/qinweixi 上短程续训 300 iters，数据含纠错样本后必须续训一次）
set -e
cd "$(dirname "$0")"

MODEL="Qwen/Qwen3.5-4B"        # 2026-08-07 基座升级：Qwen3.5（VLM，MLX 需支持 Qwen3_5 架构——首次运行如报架构不支持，说明 mlx-lm 版本过旧，需升级 mlx-lm）
ADAPTER="adapters/qinweixi"    # LoRA 输出目录
RESUME_ARGS=()

# 先准备数据（已生成过会跳过；改了 01_prepare_data.py 后删掉 data/train.jsonl 重跑）
[ -f data/train.jsonl ] || python 01_prepare_data.py --new-only

# 2026-08-06 T14：M3 路线冻结规则 iters = 1.5 × N（v3 矩阵版 train 153 条 → 230 iters）
ITERS=$(( $(wc -l < data/train.jsonl) * 3 / 2 ))

if [ -n "$RESUME" ] && [ -d "$ADAPTER" ]; then
  ITERS=300
  RESUME_ARGS=(--resume-adapter "$ADAPTER")
  echo "=== 续训模式: 在已有 $ADAPTER 上补训 $ITERS iters（称呼纠错数据）==="
fi

mlx_lm.lora \
  --model "$MODEL" \
  --train \
  --data data \
  --adapter-path "$ADAPTER" \
  --iters "$ITERS" \
  --batch-size 1 \
  --num-layers 24 \
  --lora-rank 16 \
  --learning-rate 1e-5 \
  --steps-per-report 20 \
  --steps-per-eval 100 \
  --save-every 100 \
  --max-seq-len 2048 \
  --seed 42 \
  "${RESUME_ARGS[@]}"

echo ""
echo "=== 训练完成 → $ADAPTER ==="
echo "下一步: ./03_fuse_gguf.sh"

