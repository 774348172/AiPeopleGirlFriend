#!/bin/bash
# 03_fuse_gguf.sh — 融合 LoRA → 转 GGUF → 量化 Q4_K_M → 玩家端部署
# 需要: git + python + (llama.cpp 编译好的 llama-quantize)
set -e
cd "$(dirname "$0")"

# 阶段 5（E-1，2026-08-08）：基座与 02_train.sh 统一为 Qwen3.5-4B（freeze03 契约）
MODEL="Qwen/Qwen3.5-4B"
ADAPTER="adapters/qinweixi"
FUSED="fused_qinweixi"          # 融合后 HF 格式目录
GGUF_DIR="gguf"

echo "=== 1/4 融合 LoRA 进基座 ==="
mlx_lm.fuse --model "$MODEL" --adapter-path "$ADAPTER" --save-path "$FUSED"

echo "=== 2/4 准备 llama.cpp ==="
if [ ! -d llama.cpp ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp.git
  # 编译 quantize 工具（M3 Max）
  (cd llama.cpp && cmake -B build -DLLAMA_METAL=ON && cmake --build build --config Release -j)
fi
pip install -r llama.cpp/requirements.txt

echo "=== 3/4 转 GGUF (f16) ==="
mkdir -p "$GGUF_DIR"
python llama.cpp/convert_hf_to_gguf.py "$FUSED" \
  --outfile "$GGUF_DIR/qinweixi-f16.gguf" --outtype f16

echo "=== 4/4 量化 Q4_K_M ==="
./llama.cpp/build/bin/llama-quantize \
  "$GGUF_DIR/qinweixi-f16.gguf" "$GGUF_DIR/qinweixi-q4_k_m.gguf" Q4_K_M

echo ""
echo "=== 完成 → $GGUF_DIR/qinweixi-q4_k_m.gguf (~2.3GB) ==="
echo "部署: 见 README.md 玩家端 Ollama 步骤"
